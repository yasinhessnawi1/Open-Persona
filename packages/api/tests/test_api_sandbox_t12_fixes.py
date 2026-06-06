"""Regression tests for the T12 fixes (spec 12 T12 close-out).

Each test pins a fix that closes a finding from the multi-perspective
adversarial review workflow:

- ``test_wall_clock_timeout_fires_*`` — F-T12-RES-02 (HIGH; STRUCTURAL-CLEAR).
  Spec §9 #8 requires the wall-clock cap to actually kill long-running code.
  The E2B SDK's ``run_code(timeout=...)`` maps to httpx read-timeout, not
  substrate wall-clock; T12 measured a ``while True: pass`` hanging > 90 s.
  Fix: wrap ``asyncio.to_thread(_execute_sync, ...)`` in
  :func:`asyncio.wait_for` with a 2 s grace; on timeout, force-kill the
  substrate session (if stateful) and raise :class:`ExecutionTimeoutError`.

- ``test_session_request_context_rejects_colon_in_owner_id`` /
  ``test_session_request_context_rejects_colon_in_conversation_id`` /
  ``test_pool_acquire_rejects_colon_in_*`` — F-T12-INT-01 (MEDIUM;
  STRUCTURAL-CLEAR). Cross-tenant session_id collision when either field
  contains ``:``. Fixed at both layers (context dataclass __post_init__ +
  pool._make_session_id) for defense-in-depth.

- ``test_create_sandbox_warns_when_user_limits_below_substrate_floor`` —
  F-T12-RES-01 documentation (HIGH; STRUCTURAL-CLEAR). The E2B SDK silently
  drops ``memory_mb`` / ``disk_mb`` / ``cpu_cores`` because there's no SDK
  kwarg for them — the substrate enforces its template-class floor (SCP-12-4).
  Fix: warn at construction so production telemetry surfaces the gap.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import pytest_asyncio
from persona.sandbox.errors import ExecutionTimeoutError
from persona.sandbox.result import ExecutionResult, NetworkPolicy, ResourceLimits, SandboxFile
from persona_api.sandbox import (
    HostedSandbox,
    SandboxPool,
    SandboxRequestContext,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ============================================================ shared fakes


class _SlowFakeSandbox:
    """CodeSandbox fake whose execute() blocks indefinitely.

    Used to exercise the wall-clock timeout path: the wrapper around
    ``asyncio.to_thread`` must surface :class:`TimeoutError` so the
    F-T12-RES-02 fix converts it to :class:`ExecutionTimeoutError`.
    """

    def __init__(self) -> None:
        self.created: list[str] = []
        self.destroyed: list[str] = []
        self.execute_calls: list[dict[str, object]] = []
        self.aclose_calls = 0

    async def execute(
        self,
        code: str,
        *,
        language: str = "python",  # noqa: ARG002
        session_id: str | None = None,
        timeout_s: float = 30.0,  # noqa: ARG002
        limits: ResourceLimits | None = None,  # noqa: ARG002
        network: NetworkPolicy | None = None,  # noqa: ARG002
        input_files: list[SandboxFile] | None = None,  # noqa: ARG002
    ) -> ExecutionResult:
        self.execute_calls.append({"code": code, "session_id": session_id})
        # Sleep way longer than any reasonable timeout — the wait_for guard
        # must fire BEFORE this returns.
        await asyncio.sleep(60.0)
        return ExecutionResult(
            stdout="should never reach here",
            stderr="",
            exit_status=0,
            outcome="ok",
            duration_ms=60000.0,
        )

    async def create_session(
        self,
        session_id: str,
        *,
        limits: ResourceLimits,  # noqa: ARG002
        network: NetworkPolicy,  # noqa: ARG002
    ) -> None:
        self.created.append(session_id)

    async def destroy_session(self, session_id: str) -> None:
        self.destroyed.append(session_id)

    async def aclose(self) -> None:
        self.aclose_calls += 1


# ========================================================== F-T12-RES-02


@pytest.mark.asyncio
async def test_wall_clock_timeout_fires_via_wait_for_on_hanging_execute() -> None:
    """F-T12-RES-02: wall_clock_s actually kills hung executions.

    Constructs a HostedSandbox whose _execute_sync (via asyncio.to_thread)
    is patched to sleep for 60s — longer than the wall_clock_s + grace.
    The wrapper must raise ExecutionTimeoutError before the sleep finishes.
    """
    sandbox = HostedSandbox()

    def _slow_execute_sync(*_a: object, **_k: object) -> ExecutionResult:
        # Sync sleep — runs in the worker thread; the async wait_for in
        # execute() must fire on the wall-clock+grace deadline.
        import time

        time.sleep(60.0)
        return ExecutionResult(stdout="", stderr="", exit_status=0, outcome="ok", duration_ms=0.0)

    # Patch the sync internal so we don't touch the SDK; the wait_for guard
    # is what's under test.
    with patch.object(sandbox, "_execute_sync", _slow_execute_sync):
        start = asyncio.get_event_loop().time()
        with pytest.raises(ExecutionTimeoutError) as excinfo:
            await sandbox.execute("hang_forever()", timeout_s=0.5)
        elapsed = asyncio.get_event_loop().time() - start

    # Must fire near the wall_clock_s + 2s grace window, NOT after 60s.
    assert elapsed < 5.0, f"timeout did not fire promptly; elapsed={elapsed:.2f}s"
    ctx = excinfo.value.context
    assert ctx["wall_clock_s"] == "0.5"
    assert "substrate kill forced" in excinfo.value.message


@pytest.mark.asyncio
async def test_wall_clock_timeout_kills_stateful_session() -> None:
    """F-T12-RES-02: stateful timeout cleans up the substrate session ref."""
    sandbox = HostedSandbox()

    # Seed a fake session reference; the timeout path should pop + kill it.
    class _FakeSession:
        def __init__(self) -> None:
            self.killed = False

        def kill(self) -> None:
            self.killed = True

    fake_session = _FakeSession()
    sandbox._sessions["alice:c1"] = fake_session  # type: ignore[assignment]  # noqa: SLF001

    def _slow_execute_sync(*_a: object, **_k: object) -> ExecutionResult:
        import time

        time.sleep(60.0)
        return ExecutionResult(stdout="", stderr="", exit_status=0, outcome="ok", duration_ms=0.0)

    with (
        patch.object(sandbox, "_execute_sync", _slow_execute_sync),
        pytest.raises(ExecutionTimeoutError),
    ):
        await sandbox.execute("hang()", session_id="alice:c1", timeout_s=0.5)

    # Session must be removed from the substrate-ref dict and killed.
    assert "alice:c1" not in sandbox._sessions  # noqa: SLF001
    assert fake_session.killed is True


# ========================================================== F-T12-INT-01


def test_sandbox_request_context_rejects_colon_in_owner_id() -> None:
    """F-T12-INT-01: a `:` in owner_id is the cross-tenant collision trigger."""
    with pytest.raises(ValueError, match="owner_id must not contain ':'"):
        SandboxRequestContext(owner_id="alice:bob", conversation_id="c1")


def test_sandbox_request_context_rejects_colon_in_conversation_id() -> None:
    with pytest.raises(ValueError, match="conversation_id must not contain ':'"):
        SandboxRequestContext(owner_id="alice", conversation_id="bob:c1")


def test_sandbox_request_context_accepts_normal_ids() -> None:
    """Smoke: the validator doesn't reject realistic Clerk/UUID ingress shapes."""
    ctx = SandboxRequestContext(owner_id="user_2NjdkXkLm9", conversation_id="conv_abc123def456")
    assert ctx.session_id == "user_2NjdkXkLm9:conv_abc123def456"


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[SandboxPool]:
    """Pool wrapping the slow fake; aclose on exit."""
    p = SandboxPool(
        sandbox=_SlowFakeSandbox(),
        max_per_user=2,
        idle_timeout_s=60.0,
        reap_interval_s=60.0,
    )
    try:
        yield p
    finally:
        await p.aclose()


@pytest.mark.asyncio
async def test_pool_acquire_rejects_colon_in_user_id(pool: SandboxPool) -> None:
    """F-T12-INT-01 belt-and-braces: defense-in-depth at the pool boundary too.

    The :class:`SandboxRequestContext` is the primary boundary, but direct
    callers (CLI, tests, future code paths) may construct (user_id, conv_id)
    pairs that bypass the context. The pool re-validates at acquire().
    """
    with pytest.raises(ValueError, match="user_id must not contain ':'"):
        await pool.acquire(user_id="alice:bob", conversation_id="c1")


@pytest.mark.asyncio
async def test_pool_acquire_rejects_colon_in_conversation_id(pool: SandboxPool) -> None:
    with pytest.raises(ValueError, match="conversation_id must not contain ':'"):
        await pool.acquire(user_id="alice", conversation_id="bob:c1")


@pytest.mark.asyncio
async def test_pool_acquire_no_longer_collides_across_crafted_pairs(
    pool: SandboxPool,
) -> None:
    """The T12 collision scenario now raises ValueError at the boundary.

    Pre-fix: (alice, bob:c1) and (alice:bob, c1) both produced session_id
    "alice:bob:c1" and the pool shared one substrate session across the two
    tenants. Post-fix: both calls fail at the `:` validator — the collision
    surface is structurally removed.
    """
    # First crafted pair: alice + bob:c1 → conv has `:` → rejected
    with pytest.raises(ValueError, match="conversation_id"):
        await pool.acquire(user_id="alice", conversation_id="bob:c1")
    # Second crafted pair: alice:bob + c1 → user has `:` → rejected
    with pytest.raises(ValueError, match="user_id"):
        await pool.acquire(user_id="alice:bob", conversation_id="c1")


# ========================================================== F-T12-RES-01 (documentation)


def test_create_sandbox_warns_when_user_limits_below_substrate_floor() -> None:
    """F-T12-RES-01: warning surfaces when caps are advisory only (SCP-12-4).

    The E2B SDK has no kwarg for ``memory_mb`` / ``disk_mb`` / ``cpu_cores``
    so user-supplied caps are silently dropped at the SDK boundary. The
    warning makes production telemetry aware of the gap; actual enforcement
    requires a custom E2B template (D-12-12 follow-up).

    Implementation: mock the module logger and assert the warning call carried
    the SCP-12-4 contract shape. Avoids the caplog-vs-loguru bridge
    brittleness from T09c.
    """
    from persona_api.sandbox import hosted as hosted_mod

    sandbox = HostedSandbox()
    with (
        patch.object(hosted_mod._logger, "warning") as mock_warning,  # noqa: SLF001
        patch("e2b_code_interpreter.Sandbox", side_effect=RuntimeError("stub")),
        pytest.raises(Exception),  # noqa: B017, PT011 — SDK stub raises
    ):
        sandbox._create_sandbox(  # noqa: SLF001 — testing internal warning path
            limits=ResourceLimits(memory_mb=512, disk_mb=256),
            network=NetworkPolicy(),
        )
    # The advisory warning must be one of the calls and carry the SCP-12-4
    # marker so future log aggregation can pick it out unambiguously.
    advisory_calls = [c for c in mock_warning.call_args_list if "SCP-12-4" in str(c.args)]
    assert advisory_calls, (
        f"expected SCP-12-4 advisory-cap warning; got: {mock_warning.call_args_list}"
    )
    # The call's kwargs must surface the substrate floor and the requested
    # cap so the gap is auditable.
    advisory_kwargs = advisory_calls[0].kwargs
    assert advisory_kwargs.get("requested_memory_mb") == 512
    assert advisory_kwargs.get("substrate_memory_floor_mb") == 2048
