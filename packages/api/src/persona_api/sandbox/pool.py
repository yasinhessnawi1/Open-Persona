"""SandboxPool — lifecycle, per-user cap, and idle reaper for hosted sandboxes (T09a).

Composes the :class:`persona.sandbox.protocol.CodeSandbox` Protocol — NOT
:class:`HostedSandbox` directly — so unit tests fake the substrate and a future
backend swap (D-12-12 reopen path: Daytona → self-Fly) carries the pool unchanged.

**SCP-12-1..3 reference (D-12-12 substrate-class properties):** the per-VM
attack surface (Firecracker MMDS open-but-empty, loopback OpenSSH, 26 listening
ports) is *sandbox-internal* and does NOT compound across slots — user A's
sessions never expose anything to user B by virtue of holding multiple
sandboxes. The per-user cap therefore bounds **multi-tenant** attack surface
(monopolisation of the substrate concurrency budget; quota-driven cost spikes
from a single tenant), not per-VM hardening. The cap default of 2 keeps a
single user from monopolising the E2B Hobby concurrent-sandbox budget
(D-12-12 Gate 2 measured ≥20 concurrent OK at p95<5s); production tunes this
against Spec-08 rate-limit middleware in T09c.

**Ownership invariant:** the composition root passes a constructed
:class:`CodeSandbox` into the pool; :meth:`SandboxPool.aclose` closes it.
Nothing outside the pool should call ``sandbox.create_session`` /
``destroy_session`` after the pool is wrapped around it — Gate 4 mid-exec
kill cleanliness (D-12-12) inherits through the reaper this way.

**What T09a/b ship together:**

- **T09a (bare lifecycle interface):** ``acquire`` / ``release`` /
  ``reap_idle`` (pure-function form: takes ``now``) / ``aclose`` — with
  per-user cap enforced at the acquire boundary, internal session bookkeeping,
  and an injectable clock.
- **T09b (background reaper + env config, this layer):** explicit ``start()``
  that spawns the pool-owned ``asyncio.Task`` running ``_reap_loop`` at the
  ``reap_interval_s`` cadence (D-12-17 lock: 60s default; env-configurable
  via ``PERSONA_SANDBOX_REAP_INTERVAL_S``); ``aclose`` cancels the reaper
  first, then drains sessions, then closes the substrate. The reaper survives
  individual sweep errors (one bad ``reap_idle`` call doesn't kill the loop).

**Warm-pool size = 0 at v0.1 (D-12-17):** no idle slots maintained; the first
``acquire()`` pays the substrate cold-start within the acquire call
(lazy-eager prewarm; the substrate cold-start IS the eager-prewarm). No
separate ``prewarm()`` hook — see D-12-17 rejected-alternative note for the
three reasons. Flipping to warm > 0 in production requires a follow-up spec
when the D-12-17 telemetry flip-trigger fires.

**T09c (next):** wires the per-user cap into Spec-08's rate-limit middleware
with structured audit. **T09d (after):** live E2B Hobby smoke (cost-capped).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona.sandbox.errors import (
    SandboxQuotaExceededError,
    SandboxUnavailableError,
)
from persona.sandbox.result import NetworkPolicy, ResourceLimits
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from persona.sandbox.protocol import CodeSandbox

__all__ = ["SandboxHandle", "SandboxPool"]

_logger = get_logger("sandbox.pool")


class SandboxHandle(BaseModel):
    """Receipt of a successful :meth:`SandboxPool.acquire`.

    The caller passes it back to :meth:`SandboxPool.release` on completion.
    Frozen + ``extra="forbid"`` — the handle is an immutable receipt; mutable
    freshness state (last-used, active-flag) lives in pool-internal storage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(
        description=(
            "Tenant-scoped session id ``{user_id}:{conversation_id}`` (spec 12 kickoff trip-up #6)."
        )
    )
    user_id: str = Field(description="The tenant who owns the session.")
    conversation_id: str = Field(description="The conversation this session is bound to.")


class _SessionState:
    """Pool-internal mutable bookkeeping for one live session."""

    __slots__ = ("created_at", "last_used_at", "user_id")

    def __init__(self, *, user_id: str, now: float) -> None:
        self.user_id = user_id
        self.created_at = now
        self.last_used_at = now

    def touch(self, now: float) -> None:
        self.last_used_at = now


_DEFAULT_LIMITS = ResourceLimits()
_DEFAULT_NETWORK = NetworkPolicy()


class SandboxPool:
    """Multi-tenant lifecycle manager over a single :class:`CodeSandbox`.

    Args:
        sandbox: The substrate-backed sandbox (production: :class:`HostedSandbox`;
            tests: a fake satisfying the :class:`CodeSandbox` Protocol).
        max_per_user: Per-tenant concurrent-session cap. Default ``2`` keeps a
            single user from monopolising E2B Hobby's ~30-sandbox concurrent
            budget (D-12-12 Gate 2). Bounds **multi-tenant** attack surface;
            per-VM properties (SCP-12-1..3) are sandbox-internal and don't
            cross slot boundaries.
        idle_timeout_s: Seconds without activity after which :meth:`reap_idle`
            destroys the session. Default 300s (5 min) per D-12-17; matches
            E2B's default sandbox timeout.
        reap_interval_s: Cadence at which the background reaper task calls
            :meth:`reap_idle`. Default 60s per D-12-17; env-configurable via
            ``PERSONA_SANDBOX_REAP_INTERVAL_S``.
        clock: Time source for last-used bookkeeping. Defaults to
            :func:`time.monotonic`; tests inject a fake clock to avoid sleeping.
        default_limits / default_network: Defaults applied when
            :meth:`acquire` is called without per-call overrides.
    """

    def __init__(
        self,
        *,
        sandbox: CodeSandbox,
        max_per_user: int = 2,
        idle_timeout_s: float = 300.0,
        reap_interval_s: float = 60.0,
        clock: Callable[[], float] | None = None,
        default_limits: ResourceLimits | None = None,
        default_network: NetworkPolicy | None = None,
    ) -> None:
        if max_per_user < 1:
            msg = f"max_per_user must be >= 1; got {max_per_user}"
            raise ValueError(msg)
        if idle_timeout_s <= 0:
            msg = f"idle_timeout_s must be > 0; got {idle_timeout_s}"
            raise ValueError(msg)
        if reap_interval_s <= 0:
            msg = f"reap_interval_s must be > 0; got {reap_interval_s}"
            raise ValueError(msg)
        self._sandbox = sandbox
        self._max_per_user = max_per_user
        self._idle_timeout_s = idle_timeout_s
        self._reap_interval_s = reap_interval_s
        self._clock: Callable[[], float] = clock or time.monotonic
        self._default_limits = default_limits or _DEFAULT_LIMITS
        self._default_network = default_network or _DEFAULT_NETWORK
        self._sessions: dict[str, _SessionState] = {}
        self._user_counts: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()
        self._closed = False
        # Pool-owned background reaper task (T09b). None until :meth:`start`
        # spawns it; cancelled by :meth:`aclose`. Single-ownership invariant
        # (D-12-17 reaper-ownership lock): the pool's lifecycle IS the
        # reaper's lifecycle.
        self._reaper_task: asyncio.Task[None] | None = None
        _logger.debug(
            "SandboxPool initialised",
            max_per_user=max_per_user,
            idle_timeout_s=idle_timeout_s,
            reap_interval_s=reap_interval_s,
        )

    @staticmethod
    def _make_session_id(user_id: str, conversation_id: str) -> str:
        """Build the tenant-scoped session key (kickoff trip-up #6).

        **T12 F-T12-INT-01 belt-and-braces guard (MEDIUM):** rejects ``:`` in
        either field with a domain ``ValueError``. The primary check lives at
        :class:`SandboxRequestContext.__post_init__`; this redundant guard
        protects the pool against direct ``acquire()`` callers that bypass the
        context wrapper (CLI / tests / future code paths). Defense-in-depth.
        """
        if ":" in user_id:
            msg = f"user_id must not contain ':' (got {user_id!r})"
            raise ValueError(msg)
        if ":" in conversation_id:
            msg = f"conversation_id must not contain ':' (got {conversation_id!r})"
            raise ValueError(msg)
        return f"{user_id}:{conversation_id}"

    @property
    def sandbox(self) -> CodeSandbox:
        """The underlying :class:`CodeSandbox` (read-only handle for tool factory).

        Exposed so :func:`make_pool_code_execution_tool` can pass it into the T03
        ``make_code_execution_tool`` body — the tool dispatches against this
        substrate, the pool wraps lifecycle around it. Callers MUST NOT invoke
        ``sandbox.create_session`` / ``destroy_session`` / ``aclose`` directly —
        the pool owns those (composition-root-owns-lifecycle invariant; Gate-4
        mid-exec-kill cleanliness inherits through the pool).
        """
        return self._sandbox

    async def start(self) -> None:
        """Spawn the pool-owned background reaper task. Idempotent.

        Must be called from inside a running event loop — the FastAPI lifespan
        is the canonical caller (composition root, ``app.py`` ``_lifespan``).
        Calling on a closed pool raises :class:`SandboxUnavailableError`.

        Idempotent: a second call while the reaper task is still alive is a
        no-op (the task isn't respawned). This makes the lifespan integration
        crash-safe on retried startups.
        """
        if self._closed:
            msg = "SandboxPool is closed; cannot start reaper"
            raise SandboxUnavailableError(msg, context={"reason": "pool_closed"})
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        self._reaper_task = asyncio.create_task(self._reap_loop(), name="sandbox-pool-reaper")
        _logger.info(
            "sandbox pool reaper started",
            reap_interval_s=self._reap_interval_s,
            idle_timeout_s=self._idle_timeout_s,
        )

    async def _reap_loop(self) -> None:
        """Background loop: sleep, reap, repeat. Cancelled by :meth:`aclose`.

        Survives individual sweep errors — a `reap_idle` raise is logged and
        the loop continues. The only paths that exit the loop cleanly are
        :meth:`aclose` (which cancels the task) and the pool being marked
        closed (defensive double-check).
        """
        while True:
            try:
                await asyncio.sleep(self._reap_interval_s)
            except asyncio.CancelledError:
                raise
            if self._closed:
                return
            try:
                await self.reap_idle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reaper survives single-sweep errors
                _logger.warning(
                    "sandbox pool reaper sweep failed; continuing",
                    exc_type=type(exc).__name__,
                    msg=str(exc)[:200],
                )

    async def acquire(
        self,
        *,
        user_id: str,
        conversation_id: str,
        limits: ResourceLimits | None = None,
        network: NetworkPolicy | None = None,
    ) -> SandboxHandle:
        """Acquire (or re-acquire) a session for ``(user_id, conversation_id)``.

        Idempotent on the pair: re-acquiring an already-live session returns
        its handle and bumps its last-used timestamp (so the reaper sees it
        fresh) — no new substrate sandbox is spawned. A new session is created
        only when the per-user cap has room; otherwise
        :class:`SandboxQuotaExceededError` (structured ``context``).

        Raises:
            SandboxUnavailableError: When the pool has been closed.
            SandboxQuotaExceededError: When ``user_id`` already holds
                ``max_per_user`` sessions.
        """
        if self._closed:
            msg = "SandboxPool is closed"
            raise SandboxUnavailableError(msg, context={"reason": "pool_closed"})

        session_id = self._make_session_id(user_id, conversation_id)
        chosen_limits = limits or self._default_limits
        chosen_network = network or self._default_network

        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                existing.touch(self._clock())
                return SandboxHandle(
                    session_id=session_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
            current = self._user_counts[user_id]
            if current >= self._max_per_user:
                # Structured telemetry for D-12-17 cap flip-trigger
                # ("≥5% legitimate rejections triggers per-user cap 2→3+").
                # Emit BEFORE raising so log aggregation captures even when
                # the caller swallows the exception (e.g., the tool dispatcher
                # converts it to a structured ToolResult). The log line shape
                # is the contract production telemetry aggregates on.
                _logger.info(
                    "sandbox quota rejection",
                    event="sandbox_quota_rejection",
                    user_id=user_id,
                    current_count=current,
                    cap=self._max_per_user,
                )
                msg = (
                    f"user {user_id!r} already holds {current} sandbox(es); "
                    f"cap is {self._max_per_user}"
                )
                raise SandboxQuotaExceededError(
                    msg,
                    context={
                        "user_id": user_id,
                        "current_count": str(current),
                        "cap": str(self._max_per_user),
                    },
                )
            await self._sandbox.create_session(
                session_id, limits=chosen_limits, network=chosen_network
            )
            self._sessions[session_id] = _SessionState(user_id=user_id, now=self._clock())
            self._user_counts[user_id] = current + 1

        _logger.info(
            "sandbox session acquired",
            session_id=session_id,
            user_id=user_id,
            user_count=current + 1,
        )
        return SandboxHandle(
            session_id=session_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    async def release(self, handle: SandboxHandle) -> None:
        """Destroy the session referenced by ``handle``. Idempotent.

        Releasing an already-released (or never-acquired) handle is a no-op —
        the runtime's catch-and-convert ``_dispatch`` (D-03-3) calls release
        in a ``finally`` block, and an exception mid-acquire must not cause
        a second exception on release.
        """
        async with self._lock:
            state = self._sessions.pop(handle.session_id, None)
            if state is None:
                return
            self._decrement_user(state.user_id)
        await self._sandbox.destroy_session(handle.session_id)
        _logger.info(
            "sandbox session released",
            session_id=handle.session_id,
            user_id=handle.user_id,
        )

    async def reap_idle(self, *, now: float | None = None) -> int:
        """Destroy every session whose last-used is older than ``idle_timeout_s``.

        Pure-function form: pass ``now`` to control the reaper's notion of
        time (tests use a fake clock; the T09b background reaper passes
        :func:`time.monotonic` each tick). Returns the count of sessions
        reaped. Individual destroy failures are logged and swallowed — one
        bad session must not abort the sweep.
        """
        clock_now = now if now is not None else self._clock()
        deadline = clock_now - self._idle_timeout_s

        reaped: list[str] = []
        async with self._lock:
            for session_id, state in list(self._sessions.items()):
                if state.last_used_at < deadline:
                    reaped.append(session_id)
                    del self._sessions[session_id]
                    self._decrement_user(state.user_id)
        for session_id in reaped:
            try:
                await self._sandbox.destroy_session(session_id)
            except Exception as exc:  # noqa: BLE001 — reaper continues on individual errors
                _logger.warning(
                    "reaper destroy_session failed",
                    session_id=session_id,
                    exc_type=type(exc).__name__,
                )
        if reaped:
            _logger.info("sandbox reaper swept", reaped_count=len(reaped))
        return len(reaped)

    async def aclose(self) -> None:
        """Cancel reaper, destroy every live session, close substrate; idempotent.

        Shutdown order (load-bearing):

        1. Mark ``_closed`` first — any in-flight reap or acquire sees the flag.
        2. Cancel the background reaper task (if started) and await its exit.
           Done first so it can't fire mid-shutdown and race the session sweep.
        3. Drain live sessions via ``destroy_session`` — individual failures
           logged and swallowed (one bad substrate call must not abort shutdown).
        4. Close the underlying sandbox via ``aclose`` (D-12-12 Gate 4
           mid-exec kill cleanliness inherits — reaper + final-drain + aclose
           all funnel through the same SDK ``destroy_session`` / ``aclose``
           paths that Gate 4 verified).
        """
        if self._closed:
            return
        self._closed = True
        # 1. Cancel reaper first so it can't race the session sweep.
        if self._reaper_task is not None and not self._reaper_task.done():
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 — defensive; aclose must not raise
                _logger.warning(
                    "sandbox pool reaper task raised on cancel",
                    exc_type=type(exc).__name__,
                )
        # 2. Drain live sessions under the lock so concurrent acquires can't race.
        async with self._lock:
            session_ids = list(self._sessions.keys())
            self._sessions.clear()
            self._user_counts.clear()
        for session_id in session_ids:
            try:
                await self._sandbox.destroy_session(session_id)
            except Exception as exc:  # noqa: BLE001 — aclose must not raise
                _logger.warning(
                    "aclose destroy_session failed",
                    session_id=session_id,
                    exc_type=type(exc).__name__,
                )
        await self._sandbox.aclose()

    # -- internal ----------------------------------------------------------

    def _decrement_user(self, user_id: str) -> None:
        """Decrement the per-user count; remove the key when it hits zero."""
        count = self._user_counts.get(user_id, 0)
        if count <= 1:
            self._user_counts.pop(user_id, None)
        else:
            self._user_counts[user_id] = count - 1
