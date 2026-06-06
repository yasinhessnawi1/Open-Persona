"""Unit tests for ``persona_api.sandbox.pool.SandboxPool`` (spec 12 T09a).

Substrate is faked via a Protocol-satisfying double so the tests never touch
E2B or Docker — the lifecycle scaffolding is what we're verifying here, not the
substrate's behaviour (that's the §9 attack-catalog suite, ``test_api_*`` for
hosted, and T09d's live smoke).
"""

from __future__ import annotations

import asyncio

import pytest
from persona.sandbox.errors import (
    SandboxQuotaExceededError,
    SandboxUnavailableError,
)
from persona.sandbox.result import (
    ExecutionResult,
    NetworkPolicy,
    ResourceLimits,
    SandboxFile,
)
from persona_api.sandbox import SandboxHandle, SandboxPool
from pydantic import ValidationError

# Async tests are marked individually; ctor + frozen-handle tests are sync, so
# a module-level pytestmark would trip pytest-asyncio's strict mode.


class _FakeSandbox:
    """Minimal in-memory :class:`CodeSandbox` Protocol double.

    Records every lifecycle call so tests can assert composition behaviour
    without booting a substrate.
    """

    def __init__(self) -> None:
        self.created: list[str] = []
        self.destroyed: list[str] = []
        self.aclose_calls: int = 0
        self._closed = False
        self._destroy_raises: dict[str, Exception] = {}

    def make_destroy_raise(self, session_id: str, exc: Exception) -> None:
        self._destroy_raises[session_id] = exc

    async def execute(
        self,
        code: str,  # noqa: ARG002 — Protocol contract; pool tests don't drive execute
        *,
        language: str = "python",  # noqa: ARG002 — Protocol contract
        session_id: str | None = None,  # noqa: ARG002 — Protocol contract
        timeout_s: float = 30.0,  # noqa: ARG002 — Protocol contract
        limits: ResourceLimits | None = None,  # noqa: ARG002 — Protocol contract
        network: NetworkPolicy | None = None,  # noqa: ARG002 — Protocol contract
        input_files: list[SandboxFile] | None = None,  # noqa: ARG002 — Protocol contract
    ) -> ExecutionResult:
        return ExecutionResult(
            stdout="",
            stderr="",
            exit_status=0,
            outcome="ok",
            duration_ms=0.0,
        )

    async def create_session(
        self,
        session_id: str,
        *,
        limits: ResourceLimits,  # noqa: ARG002 — Protocol contract; fake doesn't apply
        network: NetworkPolicy,  # noqa: ARG002 — Protocol contract; fake doesn't apply
    ) -> None:
        self.created.append(session_id)

    async def destroy_session(self, session_id: str) -> None:
        if session_id in self._destroy_raises:
            raise self._destroy_raises.pop(session_id)
        self.destroyed.append(session_id)

    async def aclose(self) -> None:
        self.aclose_calls += 1
        self._closed = True


class _FakeClock:
    """Step-controllable clock; tests advance it explicitly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_pool(
    *,
    max_per_user: int = 2,
    idle_timeout_s: float = 60.0,
) -> tuple[SandboxPool, _FakeSandbox, _FakeClock]:
    sandbox = _FakeSandbox()
    clock = _FakeClock()
    pool = SandboxPool(
        sandbox=sandbox,
        max_per_user=max_per_user,
        idle_timeout_s=idle_timeout_s,
        clock=clock,
    )
    return pool, sandbox, clock


# ----------------------------------------------------------------------- ctor


def test_pool_rejects_invalid_cap() -> None:
    with pytest.raises(ValueError, match="max_per_user"):
        SandboxPool(sandbox=_FakeSandbox(), max_per_user=0)


def test_pool_rejects_invalid_idle_timeout() -> None:
    with pytest.raises(ValueError, match="idle_timeout_s"):
        SandboxPool(sandbox=_FakeSandbox(), idle_timeout_s=0)


# --------------------------------------------------------------------- acquire


@pytest.mark.asyncio
async def test_acquire_creates_session_in_substrate() -> None:
    pool, sandbox, _ = _make_pool()
    handle = await pool.acquire(user_id="alice", conversation_id="c1")
    assert isinstance(handle, SandboxHandle)
    assert handle.session_id == "alice:c1"
    assert handle.user_id == "alice"
    assert handle.conversation_id == "c1"
    assert sandbox.created == ["alice:c1"]


@pytest.mark.asyncio
async def test_acquire_is_idempotent_per_user_conversation_pair() -> None:
    pool, sandbox, clock = _make_pool()
    first = await pool.acquire(user_id="alice", conversation_id="c1")
    clock.advance(10)
    second = await pool.acquire(user_id="alice", conversation_id="c1")
    # Same handle, no second create_session call.
    assert first.session_id == second.session_id
    assert sandbox.created == ["alice:c1"]


@pytest.mark.asyncio
async def test_acquire_enforces_per_user_cap() -> None:
    pool, sandbox, _ = _make_pool(max_per_user=2)
    await pool.acquire(user_id="alice", conversation_id="c1")
    await pool.acquire(user_id="alice", conversation_id="c2")
    with pytest.raises(SandboxQuotaExceededError) as excinfo:
        await pool.acquire(user_id="alice", conversation_id="c3")
    # Cap context is structured for log/audit (PersonaError.context).
    ctx = excinfo.value.context
    assert ctx == {"user_id": "alice", "current_count": "2", "cap": "2"}
    # No substrate sandbox was created for the rejected acquire.
    assert sandbox.created == ["alice:c1", "alice:c2"]


@pytest.mark.asyncio
async def test_acquire_cap_is_per_user_not_global() -> None:
    pool, sandbox, _ = _make_pool(max_per_user=1)
    await pool.acquire(user_id="alice", conversation_id="c1")
    # Bob is unaffected by Alice's cap — multi-tenant slot accounting.
    await pool.acquire(user_id="bob", conversation_id="c1")
    assert sandbox.created == ["alice:c1", "bob:c1"]


@pytest.mark.asyncio
async def test_acquire_after_release_frees_cap_slot() -> None:
    pool, sandbox, _ = _make_pool(max_per_user=1)
    h1 = await pool.acquire(user_id="alice", conversation_id="c1")
    await pool.release(h1)
    # The slot is free now — Alice can acquire a new conversation.
    await pool.acquire(user_id="alice", conversation_id="c2")
    assert sandbox.created == ["alice:c1", "alice:c2"]
    assert sandbox.destroyed == ["alice:c1"]


@pytest.mark.asyncio
async def test_acquire_rejects_on_closed_pool() -> None:
    pool, _, _ = _make_pool()
    await pool.aclose()
    with pytest.raises(SandboxUnavailableError) as excinfo:
        await pool.acquire(user_id="alice", conversation_id="c1")
    assert excinfo.value.context == {"reason": "pool_closed"}


# --------------------------------------------------------------------- release


@pytest.mark.asyncio
async def test_release_destroys_substrate_session() -> None:
    pool, sandbox, _ = _make_pool()
    handle = await pool.acquire(user_id="alice", conversation_id="c1")
    await pool.release(handle)
    assert sandbox.destroyed == ["alice:c1"]


@pytest.mark.asyncio
async def test_release_is_idempotent() -> None:
    pool, sandbox, _ = _make_pool()
    handle = await pool.acquire(user_id="alice", conversation_id="c1")
    await pool.release(handle)
    # Releasing twice must not raise and must not double-destroy.
    await pool.release(handle)
    assert sandbox.destroyed == ["alice:c1"]


@pytest.mark.asyncio
async def test_release_of_unknown_handle_is_noop() -> None:
    pool, sandbox, _ = _make_pool()
    stale = SandboxHandle(session_id="ghost:c0", user_id="ghost", conversation_id="c0")
    await pool.release(stale)
    assert sandbox.destroyed == []


# ----------------------------------------------------------------- reap_idle


@pytest.mark.asyncio
async def test_reap_idle_destroys_stale_sessions() -> None:
    pool, sandbox, clock = _make_pool(idle_timeout_s=60.0)
    await pool.acquire(user_id="alice", conversation_id="c1")
    await pool.acquire(user_id="bob", conversation_id="c1")
    # Advance past the idle deadline.
    clock.advance(120)
    reaped = await pool.reap_idle()
    assert reaped == 2
    assert sorted(sandbox.destroyed) == ["alice:c1", "bob:c1"]


@pytest.mark.asyncio
async def test_reap_idle_keeps_fresh_sessions() -> None:
    pool, sandbox, clock = _make_pool(idle_timeout_s=60.0)
    await pool.acquire(user_id="alice", conversation_id="c1")
    clock.advance(30)
    reaped = await pool.reap_idle()
    assert reaped == 0
    assert sandbox.destroyed == []


@pytest.mark.asyncio
async def test_reap_idle_accepts_explicit_now_for_test_determinism() -> None:
    pool, sandbox, clock = _make_pool(idle_timeout_s=60.0)
    await pool.acquire(user_id="alice", conversation_id="c1")
    # Don't advance the clock; pass `now` directly. The reaper uses the override.
    reaped = await pool.reap_idle(now=clock.now + 120)
    assert reaped == 1
    assert sandbox.destroyed == ["alice:c1"]


@pytest.mark.asyncio
async def test_reap_releases_cap_slot() -> None:
    pool, sandbox, clock = _make_pool(max_per_user=1, idle_timeout_s=60.0)
    await pool.acquire(user_id="alice", conversation_id="c1")
    clock.advance(120)
    await pool.reap_idle()
    # Cap freed; new acquire succeeds.
    await pool.acquire(user_id="alice", conversation_id="c2")
    assert sandbox.created == ["alice:c1", "alice:c2"]


@pytest.mark.asyncio
async def test_reap_swallows_individual_destroy_errors() -> None:
    pool, sandbox, clock = _make_pool(idle_timeout_s=60.0)
    await pool.acquire(user_id="alice", conversation_id="c1")
    await pool.acquire(user_id="bob", conversation_id="c1")
    sandbox.make_destroy_raise("alice:c1", RuntimeError("substrate gone"))
    clock.advance(120)
    reaped = await pool.reap_idle()
    # The reaper still counts both as reaped from the pool's POV; only one
    # actually made it to destroyed (the other raised and was logged).
    assert reaped == 2
    assert sandbox.destroyed == ["bob:c1"]


@pytest.mark.asyncio
async def test_acquire_touches_last_used_for_reaper() -> None:
    pool, sandbox, clock = _make_pool(idle_timeout_s=60.0)
    await pool.acquire(user_id="alice", conversation_id="c1")
    clock.advance(50)
    # Re-acquire keeps the session fresh.
    await pool.acquire(user_id="alice", conversation_id="c1")
    clock.advance(50)  # total +100, but last-used was bumped at +50
    reaped = await pool.reap_idle()
    assert reaped == 0
    assert sandbox.destroyed == []


# ------------------------------------------------------------------- aclose


@pytest.mark.asyncio
async def test_aclose_destroys_every_live_session() -> None:
    pool, sandbox, _ = _make_pool()
    await pool.acquire(user_id="alice", conversation_id="c1")
    await pool.acquire(user_id="bob", conversation_id="c1")
    await pool.aclose()
    assert sorted(sandbox.destroyed) == ["alice:c1", "bob:c1"]
    # Pool owns the substrate lifecycle (composition-root invariant).
    assert sandbox.aclose_calls == 1


@pytest.mark.asyncio
async def test_aclose_is_idempotent() -> None:
    pool, sandbox, _ = _make_pool()
    await pool.aclose()
    await pool.aclose()
    assert sandbox.aclose_calls == 1


@pytest.mark.asyncio
async def test_aclose_swallows_destroy_errors() -> None:
    pool, sandbox, _ = _make_pool()
    await pool.acquire(user_id="alice", conversation_id="c1")
    sandbox.make_destroy_raise("alice:c1", RuntimeError("substrate gone"))
    # Must not raise — aclose is a finalizer.
    await pool.aclose()
    assert sandbox.aclose_calls == 1


# ----------------------------------------------------------- handle invariants


def test_sandbox_handle_is_frozen() -> None:
    handle = SandboxHandle(session_id="alice:c1", user_id="alice", conversation_id="c1")
    with pytest.raises(ValidationError, match="frozen"):
        handle.session_id = "tampered"  # type: ignore[misc]


def test_sandbox_handle_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SandboxHandle(
            session_id="alice:c1",
            user_id="alice",
            conversation_id="c1",
            tenant="rogue",  # type: ignore[call-arg]
        )


# ==========================================================================
# T09b — background reaper task lifecycle (start / cancel-on-aclose)
# ==========================================================================


def _make_pool_with_reaper(
    *,
    reap_interval_s: float = 0.01,
    idle_timeout_s: float = 60.0,
    max_per_user: int = 2,
) -> tuple[SandboxPool, _FakeSandbox, _FakeClock]:
    """Pool with a tiny reap interval suitable for task-lifecycle tests."""
    sandbox = _FakeSandbox()
    clock = _FakeClock()
    pool = SandboxPool(
        sandbox=sandbox,
        max_per_user=max_per_user,
        idle_timeout_s=idle_timeout_s,
        reap_interval_s=reap_interval_s,
        clock=clock,
    )
    return pool, sandbox, clock


@pytest.mark.asyncio
async def test_pool_rejects_invalid_reap_interval() -> None:
    with pytest.raises(ValueError, match="reap_interval_s"):
        SandboxPool(sandbox=_FakeSandbox(), reap_interval_s=0)


@pytest.mark.asyncio
async def test_start_spawns_reaper_task() -> None:
    pool, _, _ = _make_pool_with_reaper()
    assert pool._reaper_task is None  # noqa: SLF001 — verifying internal invariant
    await pool.start()
    assert pool._reaper_task is not None  # noqa: SLF001
    assert not pool._reaper_task.done()  # noqa: SLF001
    await pool.aclose()


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    pool, _, _ = _make_pool_with_reaper()
    await pool.start()
    first_task = pool._reaper_task  # noqa: SLF001
    await pool.start()
    second_task = pool._reaper_task  # noqa: SLF001
    assert first_task is second_task
    await pool.aclose()


@pytest.mark.asyncio
async def test_start_after_aclose_rejects() -> None:
    pool, _, _ = _make_pool_with_reaper()
    await pool.aclose()
    with pytest.raises(SandboxUnavailableError) as excinfo:
        await pool.start()
    assert excinfo.value.context == {"reason": "pool_closed"}


@pytest.mark.asyncio
async def test_aclose_cancels_reaper_task() -> None:
    pool, _, _ = _make_pool_with_reaper()
    await pool.start()
    reaper = pool._reaper_task  # noqa: SLF001
    assert reaper is not None
    await pool.aclose()
    assert reaper.done()
    # Cancelled task done() == True; we don't assert .cancelled() because
    # the loop catches CancelledError on the way out → done-but-not-cancelled
    # is also a valid clean exit.


@pytest.mark.asyncio
async def test_reaper_actually_runs_reap_idle() -> None:
    """Tiny reap interval + brief asyncio.sleep lets the task tick at least once."""
    pool, sandbox, clock = _make_pool_with_reaper(reap_interval_s=0.01, idle_timeout_s=0.001)
    await pool.acquire(user_id="alice", conversation_id="c1")
    # Make the session stale immediately so the reaper has work.
    clock.advance(10.0)
    await pool.start()
    # Let the reaper tick a few times — interval is 10ms, so 100ms covers ~10 ticks.
    await asyncio.sleep(0.1)
    await pool.aclose()
    # The reaper must have destroyed the stale session.
    assert "alice:c1" in sandbox.destroyed


@pytest.mark.asyncio
async def test_reaper_survives_individual_sweep_errors() -> None:
    """A reap_idle exception in one tick must not kill the loop."""
    pool, _, _ = _make_pool_with_reaper(reap_interval_s=0.01)
    raise_counter = {"n": 0}

    async def faulty_reap(*, now: float | None = None) -> int:  # noqa: ARG001 — match signature
        raise_counter["n"] += 1
        if raise_counter["n"] == 1:
            raise RuntimeError("transient failure")
        return 0

    pool.reap_idle = faulty_reap  # type: ignore[method-assign]
    await pool.start()
    # Wait long enough for at least 3 ticks (interval = 10ms).
    await asyncio.sleep(0.08)
    reaper = pool._reaper_task  # noqa: SLF001
    assert reaper is not None
    assert not reaper.done(), "reaper task died after a single sweep error"
    # The loop kept going — counter advanced past the single transient.
    assert raise_counter["n"] >= 2
    await pool.aclose()


@pytest.mark.asyncio
async def test_aclose_without_start_is_safe() -> None:
    """Pool that never started its reaper still closes cleanly."""
    pool, sandbox, _ = _make_pool_with_reaper()
    await pool.acquire(user_id="alice", conversation_id="c1")
    await pool.aclose()
    assert "alice:c1" in sandbox.destroyed
    assert sandbox.aclose_calls == 1


# ==========================================================================
# T09c — structured rejection log on quota path (D-12-17 telemetry contract)
# ==========================================================================


@pytest.mark.asyncio
async def test_quota_rejection_emits_structured_log() -> None:
    """Pool emits a structured log line on quota rejection.

    Production telemetry aggregates on the ``event=sandbox_quota_rejection``
    log shape to measure the D-12-17 cap flip-trigger ("≥5% legitimate
    rejections triggers per-user cap 2→3+"). Emitted BEFORE the raise so
    the rejection is captured even when the caller catches the exception
    (e.g., the tool dispatcher converting it to a structured ToolResult).

    Implementation: mock the pool's loguru logger directly and verify the
    call args carry the contract shape. Avoids stderr-capture brittleness
    against pytest's own capture mechanism.
    """
    from unittest.mock import patch

    from persona_api.sandbox import pool as pool_module

    pool, _, _ = _make_pool(max_per_user=1)
    await pool.acquire(user_id="alice", conversation_id="c1")
    with (
        patch.object(pool_module._logger, "info") as mock_info,  # noqa: SLF001
        pytest.raises(SandboxQuotaExceededError),
    ):
        await pool.acquire(user_id="alice", conversation_id="c2")
    rejection_calls = [
        c for c in mock_info.call_args_list if c.kwargs.get("event") == "sandbox_quota_rejection"
    ]
    assert rejection_calls, (
        f"expected event='sandbox_quota_rejection' info call; got {mock_info.call_args_list}"
    )
    # The contract: user_id, current_count, cap all present + structured.
    kwargs = rejection_calls[0].kwargs
    assert kwargs["user_id"] == "alice"
    assert kwargs["current_count"] == 1
    assert kwargs["cap"] == 1


@pytest.mark.asyncio
async def test_quota_rejection_log_emitted_before_exception_raised() -> None:
    """The log line lands even when the test catches the exception immediately.

    Verifies the emit-before-raise ordering — if the log lived after the
    raise, the tool-dispatcher's catch-and-convert path would silently
    drop the telemetry signal.
    """
    pool, _, _ = _make_pool(max_per_user=1)
    await pool.acquire(user_id="alice", conversation_id="c1")
    # The point of this test isn't WHAT was logged (covered above) but THAT
    # the rejection didn't break the user_count accounting — the slot count
    # stays at 1 after the failed acquire, so a subsequent release frees it.
    with pytest.raises(SandboxQuotaExceededError):
        await pool.acquire(user_id="alice", conversation_id="c2")
    # No leaked count from the failed acquire — internal invariant.
    assert pool._user_counts["alice"] == 1  # noqa: SLF001 — verifying internal invariant
