"""The worker service — the fourth process class (Spec A0, T4).

A long-lived process that claims durable jobs and runs them through the same
runtime the api uses (one runtime, two execution paths). This module owns the
worker's **composition root** — the worker's analogue of ``app.py``'s lifespan —
wiring the two engines that enforce the RLS boundary:

- **dispatch engine** (cross-tenant): claim/heartbeat/complete on the jobs tables
  only. v0.1 defaults to the superuser ``database_url``; point
  ``WORKER_DISPATCH_DATABASE_URL`` at a least-privilege ``job_dispatcher`` role
  to harden (pure config — D-A0-X-rls-chokepoint).
- **RLS engine** (``persona_app``, owner-scoped): the only engine handlers touch,
  via the per-job :class:`WorkerJobContext`.

The two are kept structurally apart: the dispatch engine reaches the queue, never
a handler; the RLS engine reaches handlers, never the cross-tenant claim. The
continuous poll loop + graceful drain (signals, drain bound) land in T5; T4
provides :meth:`Worker.run_once` (claim a batch, execute each) and the health
probes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import signal
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from persona.jobs import MEDIUM_LEASE, JobRegistry
from persona.logging import get_logger
from sqlalchemy import text

from persona_api.db.engine import create_db_engine
from persona_api.jobs.executor import JobExecutor
from persona_api.jobs.queue import JobQueue
from persona_api.middleware.rls_context import make_rls_engine

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from persona_api.config import APIConfig

# Signals that initiate a graceful drain: Fly sends SIGINT by default and
# SIGTERM when configured (we trap both — D-A0-5).
_DRAIN_SIGNALS = (signal.SIGTERM, signal.SIGINT)

__all__ = ["Worker", "build_worker", "make_worker_id"]

_log = get_logger("api.jobs.worker")


def make_worker_id() -> str:
    """A unique-per-process worker identity for lease ownership.

    ``host:pid:rand`` — the random suffix disambiguates a recycled PID on the same
    host (two workers must never share an id, or one could renew/complete the
    other's lease).
    """
    return f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"


class Worker:
    """Claims and executes durable jobs. Holds the two RLS-separated engines.

    Args:
        dispatch_engine: Cross-tenant engine for jobs-table dispatch ops.
        rls_engine: The ``persona_app`` engine handlers' owner-scoped contexts
            are built on (never used for claim/dispatch).
        registry: The typed handler registry.
        worker_id: This process's identity (defaults to ``host:pid``).
    """

    def __init__(
        self,
        *,
        dispatch_engine: Engine,
        rls_engine: Engine,
        registry: JobRegistry,
        worker_id: str | None = None,
        concurrency: int = 4,
        poll_interval_seconds: float = 1.0,
        poll_jitter_seconds: float = 0.5,
        claim_lease_seconds: int = MEDIUM_LEASE.lease_seconds,
        drain_seconds: float = 270.0,
        max_jobs_per_user: int = 0,
        max_jobs_global: int = 0,
        maintenance_interval_seconds: float = 30.0,
        archive_after_seconds: float = 86_400.0,
        archive_retention_seconds: float = 2_592_000.0,
    ) -> None:
        self._dispatch_engine = dispatch_engine
        self._rls_engine = rls_engine
        self._worker_id = worker_id or make_worker_id()
        self._queue = JobQueue(dispatch_engine)
        self._executor = JobExecutor(
            queue=self._queue,
            registry=registry,
            rls_engine=rls_engine,
            worker_id=self._worker_id,
        )
        self._concurrency = concurrency
        self._poll_interval = poll_interval_seconds
        self._poll_jitter = poll_jitter_seconds
        self._claim_lease_seconds = claim_lease_seconds
        self._drain_seconds = drain_seconds
        self._max_jobs_per_user = max_jobs_per_user
        self._max_jobs_global = max_jobs_global
        self._maintenance_interval = maintenance_interval_seconds
        self._archive_after = archive_after_seconds
        self._archive_retention = archive_retention_seconds
        self._draining = asyncio.Event()
        self._in_flight: set[asyncio.Task[object]] = set()
        self._last_maintenance = 0.0

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def request_drain(self) -> None:
        """Signal the run loop to stop claiming and drain. Idempotent.

        Called by the SIGTERM/SIGINT handlers (and directly by tests). Safe to
        call repeatedly — a second signal during drain is absorbed.
        """
        if not self._draining.is_set():
            _log.info("drain requested", worker_id=self._worker_id)
        self._draining.set()

    async def run_once(
        self, *, lease_seconds: int = MEDIUM_LEASE.lease_seconds, batch: int = 1
    ) -> int:
        """Claim up to ``batch`` due jobs and execute each. Returns the count run.

        The claim is a short committed transaction (T3); each job then executes
        through the RLS choke point (T4). Per-job-type lease refinement (heartbeat
        to the type's lease) and bounded async concurrency land in T5.
        """
        records = self._queue.claim(
            worker_id=self._worker_id, lease_seconds=lease_seconds, limit=batch
        )
        for record in records:
            await self._executor.execute(record)
        return len(records)

    async def run(self) -> None:
        """Run the continuous claim→execute loop until a drain signal, then drain.

        Each iteration claims only as many jobs as there are free concurrency
        slots and dispatches each as a background task, so at most ``concurrency``
        jobs run at once (D-A0-3). When nothing is due, it waits a jittered poll
        interval (so N workers don't thunder the claim query in lockstep) — but a
        drain signal wakes it immediately. On drain: stop claiming, let in-flight
        jobs finish within the drain bound, then exit; anything still running is
        cancelled and left for lease-expiry reclaim (D-A0-5).
        """
        self._install_signal_handlers()
        _log.info(
            "worker loop started",
            worker_id=self._worker_id,
            concurrency=self._concurrency,
        )
        while not self._draining.is_set():
            self._maybe_run_maintenance()
            free = self._concurrency - len(self._in_flight)
            # Claim ONE at a time (not a batch of ``free``): the fairness count is
            # evaluated against committed state, so a batch would let all its
            # candidates pass the per-user check at once and over-grab a single
            # user. One-at-a-time re-evaluates the cap per claim — exact, no
            # starvation — and the loop still fills to ``concurrency`` by iterating.
            records = (
                self._queue.claim(
                    worker_id=self._worker_id,
                    lease_seconds=self._claim_lease_seconds,
                    limit=1,
                    max_per_user=self._max_jobs_per_user,
                    max_global=self._max_jobs_global,
                )
                if free > 0
                else []
            )
            for record in records:
                task: asyncio.Task[object] = asyncio.create_task(self._executor.execute(record))
                self._in_flight.add(task)
                task.add_done_callback(self._in_flight.discard)
            if not records:
                # Nothing due (or no free slots) — wait a jittered interval, but
                # wake immediately if a drain is requested mid-sleep.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._draining.wait(), timeout=self._next_poll_delay())
        await self._drain()

    async def _drain(self) -> None:
        """Await in-flight jobs within the drain bound; cancel the rest.

        Jobs that finish within ``drain_seconds`` complete normally. Anything still
        running when the bound elapses is cancelled — its handler stops, its
        heartbeat stops, and its lease lapses so another worker reclaims it after
        the deploy (a deploy-mid-job degrades to a resume, not a loss). The same
        mechanism as a hard crash, plus a clean stop-claiming.
        """
        if not self._in_flight:
            _log.info("drained: nothing in flight", worker_id=self._worker_id)
            return
        _log.info(
            "draining in-flight jobs",
            in_flight=len(self._in_flight),
            drain_seconds=self._drain_seconds,
        )
        pending = self._in_flight
        done, still_running = await asyncio.wait(pending, timeout=self._drain_seconds)
        if still_running:
            _log.warning(
                "drain bound exceeded; cancelling for lease-expiry reclaim",
                cancelled=len(still_running),
                finished=len(done),
            )
            for task in still_running:
                task.cancel()
            # Let each cancelled execute() run its finally (GUC reset) to completion.
            await asyncio.gather(*still_running, return_exceptions=True)
        else:
            _log.info("drained cleanly", finished=len(done))

    def _maybe_run_maintenance(self) -> None:
        """Run the maintenance sweep if its cadence has elapsed (monotonic clock)."""
        elapsed = time.monotonic() - self._last_maintenance
        if elapsed >= self._maintenance_interval:
            self.run_maintenance()
            self._last_maintenance = time.monotonic()

    def run_maintenance(self) -> None:
        """Rescuer + cleaner + retention sweep (D-A0-4). Idempotent; safe per-worker.

        Reclaims expired leases (crashed/drained workers' jobs), ages terminal jobs
        older than ``archive_after`` into the cold archive, and purges archive rows
        past retention. Each worker runs this on its own cadence; the operations are
        idempotent and bounded (A1 may add single-leader election to dedupe).
        """
        now = datetime.now(UTC)
        reclaimed = self._queue.reclaim_expired(now=now)
        archived = self._queue.archive_terminal(
            older_than=now - timedelta(seconds=self._archive_after)
        )
        purged = self._queue.purge_archive(
            older_than=now - timedelta(seconds=self._archive_retention)
        )
        if reclaimed or archived or purged:
            _log.info(
                "maintenance sweep",
                worker_id=self._worker_id,
                reclaimed=reclaimed,
                archived=archived,
                purged=purged,
            )

    def _next_poll_delay(self) -> float:
        """Jittered poll interval: ``interval + U(0, jitter)`` (D-A0-3)."""
        return self._poll_interval + secrets.randbelow(1000) / 1000 * self._poll_jitter

    def _install_signal_handlers(self) -> None:
        """Trap SIGTERM/SIGINT → ``request_drain``. No-op where unsupported."""
        loop = asyncio.get_running_loop()
        for sig in _DRAIN_SIGNALS:
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(sig, self.request_drain)

    def livez(self) -> tuple[str, int]:
        """DB-free liveness — the process is up. Mirrors the api ``/livez``."""
        return ("ok", 200)

    def healthz(self) -> tuple[str, int]:
        """Readiness — BOTH engines can reach Postgres (200) or not (503).

        Probes the dispatch engine AND the RLS engine: a worker whose RLS engine
        is unreachable can claim jobs but not run handlers, which is not ready.
        """
        for engine in (self._dispatch_engine, self._rls_engine):
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            except Exception:  # noqa: BLE001 — health probe reports, never raises
                _log.warning("worker healthz: an engine is unreachable")
                return ("db_unreachable", 503)
        return ("ok", 200)

    def aclose(self) -> None:
        """Dispose both engines (shutdown). Idempotent."""
        self._dispatch_engine.dispose()
        self._rls_engine.dispose()


def build_worker(config: APIConfig, registry: JobRegistry) -> Worker:
    """Compose a :class:`Worker` from config — the worker's composition root.

    Mirrors the api lifespan's engine wiring: the cross-tenant **dispatch** engine
    from ``worker_dispatch_database_url`` (falling back to the superuser
    ``database_url`` for v0.1), and the ``persona_app`` **RLS** engine from
    ``app_database_url`` (falling back to ``database_url``). Fail-fast if no DSN
    is configured — a worker with no database is a misconfiguration, not a
    degraded mode.
    """
    dispatch_url = config.worker_dispatch_database_url or config.database_url
    if not dispatch_url:
        msg = "worker requires DATABASE_URL (or WORKER_DISPATCH_DATABASE_URL) to be set"
        raise ValueError(msg)
    # The RLS engine MUST be the non-superuser persona_app role — a superuser
    # connection bypasses RLS entirely, silently negating the tenant boundary for
    # every handler. Fall back to ``database_url`` only for single-role dev, and
    # WARN loudly so the bypass is never silent (security review T4).
    if not config.app_database_url:
        _log.warning(
            "APP_DATABASE_URL unset: worker RLS engine falls back to the superuser DSN — "
            "RLS is BYPASSED for handler execution. Set APP_DATABASE_URL (persona_app) in "
            "any multi-tenant deployment."
        )
    dispatch_engine = create_db_engine(dispatch_url)
    rls_engine = make_rls_engine(config.effective_app_database_url)
    _log.info(
        "worker composition root built",
        dispatch_role_dedicated=bool(config.worker_dispatch_database_url),
        rls_role_superuser_fallback=not bool(config.app_database_url),
        registered_types=len(registry.types()),
    )
    return Worker(
        dispatch_engine=dispatch_engine,
        rls_engine=rls_engine,
        registry=registry,
        concurrency=config.worker_concurrency,
        poll_interval_seconds=config.worker_poll_interval_seconds,
        poll_jitter_seconds=config.worker_poll_jitter_seconds,
        claim_lease_seconds=config.worker_claim_lease_seconds,
        drain_seconds=config.worker_drain_seconds,
        max_jobs_per_user=config.worker_max_jobs_per_user,
        max_jobs_global=config.worker_max_jobs_global,
        maintenance_interval_seconds=config.worker_maintenance_interval_seconds,
        archive_after_seconds=config.worker_archive_after_seconds,
        archive_retention_seconds=config.worker_archive_retention_seconds,
    )
