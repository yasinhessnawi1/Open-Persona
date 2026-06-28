"""Tests for the PolicyGatedToolbox — the A3 enforcement boundary (Spec A3, T7).

The three dispatch paths, plus the both-ways check the spec demands (criterion 1):

- **allow** → the inner tool executes (chat-time behaviour preserved);
- **deny** → a recoverable ``ToolResult(is_error=True)`` — the leg continues, no proposal, no raise;
- **gate** → the exact proposal is recorded **before** the raise, then
  ``GatedActionProposedError`` (carrying ``proposal_id`` + ``activity_status``);
- the SAME gated tool executes in a plain (chat) ``Toolbox`` but gates in the leg's
  ``PolicyGatedToolbox`` — denied-in-leg / allowed-in-chat, verified both ways;
- network-enabled ``code_execution`` escalates from allow (compute) to gate (external_mutate).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.approvals import ActionProposal, GateContext, PolicyGatedToolbox
from persona.errors import GatedActionProposedError
from persona.schema.tools import ToolCall, ToolResult
from persona.tools import (
    DEFAULT_POLICY,
    ActionCategory,
    CategoryDecision,
    CategoryPolicy,
    CategoryRule,
    Toolbox,
    tool,
)

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
_CTX = GateContext(owner_id="o1", task_id="t1", persona_id="p1")


@tool(name="web_search", description="search the web")
async def _web_search(query: str) -> ToolResult:
    return ToolResult(tool_name="web_search", content=f"results: {query}")


@tool(name="send_email", description="send an email to a third party")
async def _send_email(to: str, subject: str = "") -> ToolResult:  # noqa: ARG001 — schema-only fake
    return ToolResult(tool_name="send_email", content="sent")


@tool(name="code_execution", description="run sandboxed code")
async def _code_execution(code: str) -> ToolResult:  # noqa: ARG001 — schema-only fake
    return ToolResult(tool_name="code_execution", content="ran")


class _FakeRecorder:
    """Captures recorded proposals (the api ApprovalStore.create_proposal seam)."""

    def __init__(self) -> None:
        self.recorded: list[ActionProposal] = []

    def create_proposal(self, proposal: ActionProposal) -> ActionProposal:
        self.recorded.append(proposal)
        return proposal


def _gated(
    tools: list,  # noqa: ANN001 — heterogeneous AsyncTool fakes
    policy: CategoryPolicy,
    recorder: _FakeRecorder,
    *,
    allow_list: list[str],
    network_enabled: bool = False,
) -> PolicyGatedToolbox:
    return PolicyGatedToolbox(
        tools,
        allow_list=allow_list,
        policy=policy,
        recorder=recorder,
        context=_CTX,
        network_enabled=network_enabled,
        clock=lambda: _NOW,
    )


_DENY_EXTERNAL = CategoryPolicy(
    overrides=(
        CategoryRule(category=ActionCategory.EXTERNAL_MUTATE, decision=CategoryDecision.DENY),
    )
)


class TestAllowPath:
    async def test_allowed_category_executes(self) -> None:
        rec = _FakeRecorder()
        gated = _gated([_web_search], DEFAULT_POLICY, rec, allow_list=["web_search"])
        result = await gated.dispatch(ToolCall(name="web_search", args={"query": "fares"}))
        assert result.is_error is False
        assert result.content == "results: fares"
        assert rec.recorded == []  # no proposal for a free action


class TestDenyPath:
    async def test_denied_category_returns_recoverable_result(self) -> None:
        rec = _FakeRecorder()
        gated = _gated([_send_email], _DENY_EXTERNAL, rec, allow_list=["send_email"])
        result = await gated.dispatch(ToolCall(name="send_email", args={"to": "bob@x.com"}))
        # Recoverable: an error result the model adapts to — NOT a raise, the leg continues.
        assert result.is_error is True
        assert "unattended" in result.content
        assert rec.recorded == []  # a deny never records a proposal


class TestGatePath:
    async def test_gate_records_proposal_before_raising(self) -> None:
        rec = _FakeRecorder()
        gated = _gated([_send_email], DEFAULT_POLICY, rec, allow_list=["send_email"])
        args = {"to": "bob@x.com", "subject": "the appeal"}
        with pytest.raises(GatedActionProposedError) as excinfo:
            await gated.dispatch(ToolCall(name="send_email", args=args))
        # Recorded durably BEFORE the raise (else waiting(on_user) has nothing to resume).
        assert len(rec.recorded) == 1
        proposal = rec.recorded[0]
        assert proposal.tool_name == "send_email"
        assert proposal.arguments == args  # the EXACT payload, verbatim (replayed on approval)
        # send_email is unmapped → the gated default (external_mutate), the back-door close.
        assert ActionCategory.EXTERNAL_MUTATE in proposal.categories
        assert excinfo.value.context["proposal_id"] == proposal.proposal_id

    async def test_error_carries_awaiting_approval_activity_status(self) -> None:
        rec = _FakeRecorder()
        gated = _gated([_send_email], DEFAULT_POLICY, rec, allow_list=["send_email"])
        with pytest.raises(GatedActionProposedError) as excinfo:
            await gated.dispatch(ToolCall(name="send_email", args={"to": "x"}))
        # The ActivityStatusCarrier seam P2's outer ObservedToolbox reads (merge-back).
        assert excinfo.value.activity_status == "awaiting_approval"


class TestBothWaysChatVsLeg:
    async def test_same_tool_executes_in_chat_but_gates_in_leg(self) -> None:
        rec = _FakeRecorder()
        # Chat path: a plain Toolbox — send_email is allow-listed and just executes.
        chat = Toolbox([_send_email], allow_list=["send_email"])
        chat_result = await chat.dispatch(ToolCall(name="send_email", args={"to": "x"}))
        assert chat_result.is_error is False
        # Leg path: the SAME tool, same allow-list, gates (unattended) under the default policy.
        leg = _gated([_send_email], DEFAULT_POLICY, rec, allow_list=["send_email"])
        with pytest.raises(GatedActionProposedError):
            await leg.dispatch(ToolCall(name="send_email", args={"to": "x"}))


class TestNetworkEscalation:
    async def test_code_execution_allows_without_network(self) -> None:
        rec = _FakeRecorder()
        gated = _gated(
            [_code_execution],
            DEFAULT_POLICY,
            rec,
            allow_list=["code_execution"],
            network_enabled=False,
        )
        result = await gated.dispatch(ToolCall(name="code_execution", args={"code": "1+1"}))
        assert result.is_error is False
        assert rec.recorded == []

    async def test_code_execution_gates_with_network(self) -> None:
        rec = _FakeRecorder()
        gated = _gated(
            [_code_execution],
            DEFAULT_POLICY,
            rec,
            allow_list=["code_execution"],
            network_enabled=True,
        )
        with pytest.raises(GatedActionProposedError):
            await gated.dispatch(ToolCall(name="code_execution", args={"code": "post(...)"}))
        assert len(rec.recorded) == 1
        assert ActionCategory.EXTERNAL_MUTATE in rec.recorded[0].categories
