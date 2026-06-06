"""Unit tests for ``make_pool_code_execution_tool`` + ``make_code_execution_tool`` hooks (T10).

Covers:

- The new ``pre_execute_hook`` (T03 widening): awaited before substrate execute;
  ``SandboxError`` raised by the hook flows through the existing catch-and-convert
  path → structured ``ToolResult(is_error=True)``.
- The new ``on_execute_success`` hook: fires on outcome=="ok"; NOT fired on
  outcome="error" / "timeout" / "oom" / "killed" (mirrors D-08-6 "failed turn
  deducts nothing", applied per-execution per D-12-3).
- The api-side wrapper ``make_pool_code_execution_tool`` reads the contextvar
  for ``(owner_id, conversation_id)``; without context, no pool acquire happens
  and no credits deduct (CLI / one-shot path).
- Credits hook composition: ``credits_service.deduct`` is called with
  ``user_id=owner_id, amount=1, reason="code_execution"`` after a successful
  execute. Hook failure is logged but doesn't break the tool result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import pytest_asyncio
from persona.sandbox.errors import SandboxQuotaExceededError, SandboxUnavailableError
from persona.sandbox.result import ExecutionResult, NetworkPolicy, ResourceLimits, SandboxFile
from persona.sandbox.tool import make_code_execution_tool
from persona_api.sandbox import (
    SandboxRequestContext,
    make_pool_code_execution_tool,
    reset_sandbox_request_context,
    set_sandbox_request_context,
)
from persona_api.sandbox.pool import SandboxPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ----------------------------------------------------- shared substrate fake


class _FakeSandbox:
    """Minimal CodeSandbox satisfying the Protocol."""

    def __init__(
        self,
        *,
        outcome: str = "ok",
        execute_raises: BaseException | None = None,
    ) -> None:
        self.outcome = outcome
        self.execute_raises = execute_raises
        self.execute_calls: list[dict[str, object]] = []
        self.created: list[str] = []
        self.destroyed: list[str] = []
        self.aclose_calls: int = 0

    async def execute(
        self,
        code: str,
        *,
        language: str = "python",  # noqa: ARG002 — Protocol contract
        session_id: str | None = None,
        timeout_s: float = 30.0,  # noqa: ARG002 — Protocol contract
        limits: ResourceLimits | None = None,  # noqa: ARG002 — Protocol contract
        network: NetworkPolicy | None = None,  # noqa: ARG002 — Protocol contract
        input_files: list[SandboxFile] | None = None,  # noqa: ARG002 — Protocol contract
    ) -> ExecutionResult:
        self.execute_calls.append({"code": code, "session_id": session_id})
        if self.execute_raises is not None:
            raise self.execute_raises
        return ExecutionResult(
            stdout="hello\n",
            stderr="",
            exit_status=0,
            outcome=self.outcome,  # type: ignore[arg-type]
            duration_ms=12.3,
        )

    async def create_session(
        self,
        session_id: str,
        *,
        limits: ResourceLimits,  # noqa: ARG002 — Protocol contract
        network: NetworkPolicy,  # noqa: ARG002 — Protocol contract
    ) -> None:
        self.created.append(session_id)

    async def destroy_session(self, session_id: str) -> None:
        self.destroyed.append(session_id)

    async def aclose(self) -> None:
        self.aclose_calls += 1


@pytest_asyncio.fixture
async def pool_with_fake() -> AsyncIterator[tuple[SandboxPool, _FakeSandbox]]:
    """A pool wrapping the fake substrate; aclose on test exit."""
    fake = _FakeSandbox()
    pool = SandboxPool(sandbox=fake, max_per_user=2, idle_timeout_s=60.0, reap_interval_s=60.0)
    try:
        yield pool, fake
    finally:
        await pool.aclose()


# ============================================================ make_code_execution_tool hooks


@pytest.mark.asyncio
async def test_pre_execute_hook_awaited_before_substrate_execute() -> None:
    """The hook fires BEFORE substrate.execute (load-bearing for pool acquire)."""
    fake = _FakeSandbox()
    call_order: list[str] = []

    async def _hook() -> None:
        call_order.append("hook")

    # Wrap fake.execute to record ordering relative to hook.
    original = fake.execute

    async def _tracked_execute(*a: object, **k: object) -> ExecutionResult:
        call_order.append("execute")
        return await original(*a, **k)  # type: ignore[arg-type]

    fake.execute = _tracked_execute  # type: ignore[method-assign]

    tool = make_code_execution_tool(fake, pre_execute_hook=_hook)
    result = await tool.execute(code="print('hi')")
    assert not result.is_error
    assert call_order == ["hook", "execute"]


@pytest.mark.asyncio
async def test_pre_execute_hook_raising_sandbox_error_becomes_tool_error() -> None:
    """Pool quota / unavailability from the hook surfaces as a structured failure."""
    fake = _FakeSandbox()

    async def _hook_raises_quota() -> None:
        raise SandboxQuotaExceededError(
            "user already at cap",
            context={"user_id": "alice", "current_count": "2", "cap": "2"},
        )

    tool = make_code_execution_tool(fake, pre_execute_hook=_hook_raises_quota)
    result = await tool.execute(code="print('hi')")
    assert result.is_error
    assert "SandboxQuotaExceededError" in result.content
    # Substrate was NOT called — quota check happens before execute.
    assert fake.execute_calls == []


@pytest.mark.asyncio
async def test_on_execute_success_called_only_on_outcome_ok() -> None:
    """Hook fires on outcome=='ok'; not on error/timeout/oom/killed (D-12-3 mirror of D-08-6)."""
    hook_calls: list[ExecutionResult] = []

    async def _hook(r: ExecutionResult) -> None:
        hook_calls.append(r)

    # outcome=ok → hook fires
    fake_ok = _FakeSandbox(outcome="ok")
    tool_ok = make_code_execution_tool(fake_ok, on_execute_success=_hook)
    await tool_ok.execute(code="print('ok')")
    assert len(hook_calls) == 1

    # outcome=error → hook does NOT fire
    hook_calls.clear()
    fake_err = _FakeSandbox(outcome="error")
    tool_err = make_code_execution_tool(fake_err, on_execute_success=_hook)
    await tool_err.execute(code="raise RuntimeError('boom')")
    assert hook_calls == []


@pytest.mark.asyncio
async def test_on_execute_success_failure_logged_but_does_not_break_tool_result() -> None:
    """Credits-write outage must not corrupt the structured tool result."""
    fake = _FakeSandbox(outcome="ok")

    async def _hook_raises() -> None:
        raise RuntimeError("credits db unreachable")

    async def _hook(_r: ExecutionResult) -> None:
        await _hook_raises()

    tool = make_code_execution_tool(fake, on_execute_success=_hook)
    result = await tool.execute(code="print('ok')")
    # Tool result is still success — model sees the successful execution.
    assert not result.is_error
    assert "hello" in result.content


# =================================================== make_pool_code_execution_tool (api wrapper)


@pytest.mark.asyncio
async def test_pool_tool_without_request_context_skips_pool_acquire_and_credits(
    pool_with_fake: tuple[SandboxPool, _FakeSandbox],
) -> None:
    """CLI / one-shot path: no contextvar → no pool acquire, no credits."""
    pool, fake = pool_with_fake
    # Stub the engine — we won't actually touch the DB because no context is bound.
    with patch("persona_api.sandbox.runtime_tool.credits_service.deduct") as mock_deduct:
        tool = make_pool_code_execution_tool(pool=pool, rls_engine=object())  # type: ignore[arg-type]
        # No context set → contextvar returns None → no acquire, no deduct.
        result = await tool.execute(code="print('hi')")
        assert not result.is_error
        # Pool didn't acquire (no session created in the fake substrate via pool).
        assert pool._user_counts == {}  # noqa: SLF001 — verify internal invariant
        # Credits never deducted.
        mock_deduct.assert_not_called()


@pytest.mark.asyncio
async def test_pool_tool_with_context_acquires_pool_and_deducts_credits(
    pool_with_fake: tuple[SandboxPool, _FakeSandbox],
) -> None:
    """End-to-end: context bound → pool acquire fires + credits deducted on ok."""
    pool, fake = pool_with_fake
    fake_engine = object()  # never touched because credits_service is mocked
    token = set_sandbox_request_context(
        SandboxRequestContext(owner_id="alice", conversation_id="c1")
    )
    try:
        with patch("persona_api.sandbox.runtime_tool.credits_service.deduct") as mock_deduct:
            mock_deduct.return_value = 99  # arbitrary post-deduction balance
            tool = make_pool_code_execution_tool(pool=pool, rls_engine=fake_engine)  # type: ignore[arg-type]
            result = await tool.execute(code="print('hi')")
            assert not result.is_error
            # Pool acquired: session_id "alice:c1" in pool.
            assert "alice:c1" in pool._sessions  # noqa: SLF001 — verify integration
            # Substrate execute called with the right session_id.
            assert fake.execute_calls == [{"code": "print('hi')", "session_id": "alice:c1"}]
            # Credits deducted exactly once with the right shape.
            mock_deduct.assert_called_once()
            kwargs = mock_deduct.call_args.kwargs
            assert kwargs["user_id"] == "alice"
            assert kwargs["amount"] == 1
            assert kwargs["reason"] == "code_execution"
    finally:
        reset_sandbox_request_context(token)


@pytest.mark.asyncio
async def test_pool_tool_does_not_deduct_credits_on_substrate_error(
    pool_with_fake: tuple[SandboxPool, _FakeSandbox],
) -> None:
    """Failed execute → outcome!=ok → credits NOT deducted (D-08-6 mirror)."""
    pool, fake = pool_with_fake
    fake.outcome = "error"
    token = set_sandbox_request_context(
        SandboxRequestContext(owner_id="alice", conversation_id="c1")
    )
    try:
        with patch("persona_api.sandbox.runtime_tool.credits_service.deduct") as mock_deduct:
            tool = make_pool_code_execution_tool(pool=pool, rls_engine=object())  # type: ignore[arg-type]
            result = await tool.execute(code="boom")
            assert result.is_error
            mock_deduct.assert_not_called()
    finally:
        reset_sandbox_request_context(token)


@pytest.mark.asyncio
async def test_pool_tool_quota_rejection_surfaces_as_tool_error(
    pool_with_fake: tuple[SandboxPool, _FakeSandbox],
) -> None:
    """When pool.acquire raises SandboxQuotaExceededError, tool returns is_error=True."""
    pool, fake = pool_with_fake
    # Fill the cap for alice: 2 sessions.
    h1_token = set_sandbox_request_context(
        SandboxRequestContext(owner_id="alice", conversation_id="c1")
    )
    try:
        await pool.acquire(user_id="alice", conversation_id="c1")
        await pool.acquire(user_id="alice", conversation_id="c2")
    finally:
        reset_sandbox_request_context(h1_token)

    # Now bind a context for a NEW conversation that should trip the cap.
    token = set_sandbox_request_context(
        SandboxRequestContext(owner_id="alice", conversation_id="c3")
    )
    try:
        with patch("persona_api.sandbox.runtime_tool.credits_service.deduct") as mock_deduct:
            tool = make_pool_code_execution_tool(pool=pool, rls_engine=object())  # type: ignore[arg-type]
            result = await tool.execute(code="print('hi')")
            assert result.is_error
            assert "SandboxQuotaExceededError" in result.content
            # Substrate never called for the rejected acquire.
            assert all(c["session_id"] != "alice:c3" for c in fake.execute_calls)
            # No credits charged for a quota-rejected attempt.
            mock_deduct.assert_not_called()
    finally:
        reset_sandbox_request_context(token)


@pytest.mark.asyncio
async def test_pool_tool_substrate_unavailable_surfaces_as_tool_error(
    pool_with_fake: tuple[SandboxPool, _FakeSandbox],
) -> None:
    """SandboxUnavailableError from execute path → structured tool error, no credits."""
    pool, fake = pool_with_fake
    fake.execute_raises = SandboxUnavailableError(
        "E2B down", context={"reason": "e2b_create_failed"}
    )
    token = set_sandbox_request_context(
        SandboxRequestContext(owner_id="alice", conversation_id="c1")
    )
    try:
        with patch("persona_api.sandbox.runtime_tool.credits_service.deduct") as mock_deduct:
            tool = make_pool_code_execution_tool(pool=pool, rls_engine=object())  # type: ignore[arg-type]
            result = await tool.execute(code="print('hi')")
            assert result.is_error
            assert "SandboxUnavailableError" in result.content
            mock_deduct.assert_not_called()
    finally:
        reset_sandbox_request_context(token)


# =========================================================== contextvar invariants


def test_sandbox_request_context_session_id_shape() -> None:
    """Spec 12 kickoff trip-up #6: session_id is `{owner_id}:{conversation_id}`."""
    ctx = SandboxRequestContext(owner_id="user-42", conversation_id="conv-7")
    assert ctx.session_id == "user-42:conv-7"


def test_sandbox_request_context_is_frozen() -> None:
    """SandboxRequestContext is frozen — mutation is a programming error."""
    ctx = SandboxRequestContext(owner_id="alice", conversation_id="c1")
    with pytest.raises(Exception):  # noqa: B017, PT011 — dataclasses.FrozenInstanceError
        ctx.owner_id = "mallory"  # type: ignore[misc]


def test_contextvar_set_get_reset_round_trip() -> None:
    """Token-based set/reset must restore the prior (None) context."""
    from persona_api.sandbox import get_sandbox_request_context

    assert get_sandbox_request_context() is None
    token = set_sandbox_request_context(
        SandboxRequestContext(owner_id="alice", conversation_id="c1")
    )
    assert get_sandbox_request_context() is not None
    reset_sandbox_request_context(token)
    assert get_sandbox_request_context() is None
