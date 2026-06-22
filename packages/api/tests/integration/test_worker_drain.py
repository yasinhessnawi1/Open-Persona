"""Graceful-drain + continuous-loop + heartbeat tests (Spec A0, T5).

Against real Postgres. Proves the drain contract end-to-end:

- **finish within the drain bound** — a drain signal stops claiming and lets an
  in-flight job complete; the loop exits cleanly and the job is ``succeeded``.
- **exceed the bound → cancel → lease-expiry floor** — a job still running when
  the drain bound elapses is cancelled (its handler sees ``CancelledError``), the
  job is NOT completed, the GUC is reset, and it is left claimed for reclaim.
- **continuous loop** — the loop claims and runs a backlog, then drains.
- **heartbeat keeps a long job's lease alive** — a handler outliving the claim
  lease is not falsely reclaimed mid-run (D-A0-1).
"""

# ruff: noqa: ARG001, ARG002, SLF001 — fixtures + protocol args + private internals.
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest
from persona.jobs import JobPayload, JobRegistry, JobState, JobTypeSpec, LeasePolicy
from persona_api.jobs import JobQueue, Worker
from persona_api.jobs.executor import JobExecutor
from persona_api.middleware.rls_context import current_user_id
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from persona.jobs import JobContext

pytestmark = pytest.mark.integration


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Engine:
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set; skipping worker drain test")
    return create_engine(app_url.replace("+asyncpg", "+psycopg"))


@pytest.fixture
def seeded(migrated_engine: Engine) -> Engine:
    with migrated_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))
    return migrated_engine


class _Payload(JobPayload):
    marker: str = "m"


class _SlowHandler:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.started = asyncio.Event()
        self.finished = False
        self.cancelled = False

    async def handle(self, payload: _Payload, context: JobContext) -> None:
        self.started.set()
        try:
            await asyncio.sleep(self.seconds)
            self.finished = True
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _registry(handler: object, *, lease: LeasePolicy | None = None) -> JobRegistry:
    spec_kwargs: dict[str, object] = {
        "type": "slow",
        "payload_model": _Payload,
        "handler": handler,
        "idempotency_key": lambda p: f"slow:{p.marker}",
    }
    if lease is not None:
        spec_kwargs["lease"] = lease
    return JobRegistry([JobTypeSpec(**spec_kwargs)])  # type: ignore[arg-type]


def _enqueue(engine: Engine, marker: str) -> str:
    rec = JobQueue(engine).enqueue(
        type="slow", owner_id="user_a", payload={"marker": marker}, idempotency_key=f"slow:{marker}"
    )
    assert rec is not None
    return rec.id


def _state(engine: Engine, job_id: str) -> str:
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT state FROM jobs WHERE id = :i"), {"i": job_id}
        ).scalar_one()


def test_drain_lets_inflight_job_finish_within_bound(seeded: Engine, app_engine: Engine) -> None:
    handler = _SlowHandler(0.3)
    worker = Worker(
        dispatch_engine=seeded,
        rls_engine=app_engine,
        registry=_registry(handler),
        worker_id="w1",
        poll_interval_seconds=0.05,
        poll_jitter_seconds=0.0,
        drain_seconds=5.0,
    )
    job_id = _enqueue(seeded, "m1")

    async def drive() -> None:
        run_task = asyncio.create_task(worker.run())
        await asyncio.wait_for(handler.started.wait(), timeout=5)
        worker.request_drain()  # drain while the job is mid-flight
        await asyncio.wait_for(run_task, timeout=10)

    asyncio.run(drive())
    assert handler.finished
    assert not handler.cancelled
    assert _state(seeded, job_id) == "succeeded"


def test_drain_bound_exceeded_cancels_and_leaves_for_reclaim(
    seeded: Engine, app_engine: Engine
) -> None:
    handler = _SlowHandler(30.0)  # far longer than the drain bound
    worker = Worker(
        dispatch_engine=seeded,
        rls_engine=app_engine,
        registry=_registry(handler),
        worker_id="w1",
        poll_interval_seconds=0.05,
        poll_jitter_seconds=0.0,
        drain_seconds=0.3,  # tiny bound
    )
    job_id = _enqueue(seeded, "m1")

    async def drive() -> None:
        run_task = asyncio.create_task(worker.run())
        await asyncio.wait_for(handler.started.wait(), timeout=5)
        worker.request_drain()
        await asyncio.wait_for(run_task, timeout=10)

    asyncio.run(drive())
    assert handler.cancelled, "the over-running handler must be cancelled at the drain bound"
    assert not handler.finished
    # NOT completed; left claimed/running for lease-expiry reclaim (the floor).
    assert _state(seeded, job_id) in {"claimed", "running"}
    # The choke-point GUC was reset even through the cancellation.
    assert current_user_id.get() is None


def test_continuous_loop_processes_backlog_then_drains(seeded: Engine, app_engine: Engine) -> None:
    handler = _SlowHandler(0.05)
    worker = Worker(
        dispatch_engine=seeded,
        rls_engine=app_engine,
        registry=_registry(handler),
        worker_id="w1",
        concurrency=4,
        poll_interval_seconds=0.05,
        poll_jitter_seconds=0.0,
    )
    ids = [_enqueue(seeded, f"m{i}") for i in range(3)]

    async def drive() -> None:
        run_task = asyncio.create_task(worker.run())

        async def all_done() -> bool:
            return all(_state(seeded, i) == "succeeded" for i in ids)

        for _ in range(100):
            if await all_done():
                break
            await asyncio.sleep(0.05)
        worker.request_drain()
        await asyncio.wait_for(run_task, timeout=10)

    asyncio.run(drive())
    assert all(_state(seeded, i) == "succeeded" for i in ids)


def test_heartbeat_keeps_long_job_lease_alive(seeded: Engine, app_engine: Engine) -> None:
    # Type lease 2s / heartbeat 1s; a 2.5s handler outlives the 1s claim lease, but
    # the heartbeat renews it so a concurrent reclaim sweep does NOT steal it.
    handler = _SlowHandler(2.5)
    registry = _registry(handler, lease=LeasePolicy(lease_seconds=2, heartbeat_seconds=1))
    queue = JobQueue(seeded)
    executor = JobExecutor(queue=queue, registry=registry, rls_engine=app_engine, worker_id="w1")
    _enqueue(seeded, "m1")

    async def drive() -> tuple[int, JobState]:
        claimed = queue.claim(worker_id="w1", lease_seconds=1, limit=1)  # short bootstrap lease
        exec_task = asyncio.create_task(executor.execute(claimed[0]))
        await asyncio.sleep(1.5)  # past the 1s claim lease; heartbeat (1s) has renewed it
        reclaimed_mid = queue.reclaim_expired()
        outcome = await exec_task
        return reclaimed_mid, outcome

    reclaimed_mid, outcome = asyncio.run(drive())
    assert reclaimed_mid == 0, "heartbeat must keep the lease fresh so reclaim does not steal it"
    assert outcome is JobState.SUCCEEDED
