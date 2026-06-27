"""Tests for ObservedToolbox — the outermost activity-emit decorator (P2 T1).

The load-bearing test is the A3-seam one: a **gated** action (the wrapped target raises
``GatedActionProposedError``-shaped) must still emit ``activity_start`` then
``activity_end(status="awaiting_approval")`` — never zero events — proving emit wraps the
gate (P2-D-a3-composition). Plus: ok/error/raise paths, redaction-at-emit, kind
derivation, paired ids, and observer-failure isolation.
"""

# ruff: noqa: ARG002 — stub methods accept unused protocol args by design
from __future__ import annotations

from typing import Any

import pytest
from persona.errors import ToolNotAllowedError
from persona.schema.tools import ToolCall, ToolResult
from persona.tools.activity import (
    ActivityEnd,
    ActivityStart,
    ActivityStatus,
    DispatchContext,
    ObservedToolbox,
    activity_kind_for,
)
from persona.tools.protocol import AsyncTool, tool
from persona.tools.toolbox import Toolbox

# ---------------------------------------------------------------------------
# Section: test doubles
# ---------------------------------------------------------------------------


class RecordingObserver:
    """Captures every start/end the ObservedToolbox emits, in order."""

    def __init__(self) -> None:
        self.starts: list[ActivityStart] = []
        self.ends: list[ActivityEnd] = []
        self.order: list[str] = []

    async def on_activity_start(self, start: ActivityStart) -> None:
        self.starts.append(start)
        self.order.append("start")

    async def on_activity_end(self, end: ActivityEnd) -> None:
        self.ends.append(end)
        self.order.append("end")


class _GatedActionProposedError(Exception):
    """Stub of A3's gate exception — declares the awaiting-approval activity status."""

    @property
    def activity_status(self) -> ActivityStatus:
        return "awaiting_approval"


class StubToolbox:
    """A permissive dispatch target that does not validate args.

    Lets a test send arbitrary args (e.g. secrets/blobs) or force a raise without the
    @tool argument validation the real Toolbox applies. Optionally raises a supplied
    exception to simulate a gate/denial.
    """

    def __init__(self, *, raises: Exception | None = None, is_error: bool = False) -> None:
        self._raises = raises
        self._is_error = is_error

    async def dispatch(self, tool_call: ToolCall) -> ToolResult:
        if self._raises is not None:
            raise self._raises
        return ToolResult(
            tool_name=tool_call.name,
            content="error" if self._is_error else "ok",
            is_error=self._is_error,
        )

    def kind_for(self, tool_name: str) -> Any:  # noqa: ANN401 — forwarded, unused here
        return "builtin"

    def get_specs(self) -> list[Any]:
        return []

    def is_allowed(self, tool_name: str) -> bool:
        return True

    def names(self) -> list[str]:
        return []


def _make_echo_tool(name: str = "echo") -> AsyncTool:
    @tool(name=name, description=f"Echo for {name}")
    async def _echo(text: str) -> ToolResult:
        return ToolResult(tool_name=name, content=text)

    return _echo


# ---------------------------------------------------------------------------
# Section: the A3-seam invariant — emit wraps the gate
# ---------------------------------------------------------------------------


class TestGatedActionEmission:
    @pytest.mark.asyncio
    async def test_gated_action_emits_start_then_awaiting_approval_never_zero(self) -> None:
        # ObservedToolbox(GatedToolbox(...)) — emit is OUTERMOST. The gate raises; the
        # start must already have fired and the end must record awaiting_approval.
        obs = RecordingObserver()
        gated = StubToolbox(raises=_GatedActionProposedError())
        tb = ObservedToolbox(gated, obs)

        with pytest.raises(_GatedActionProposedError):
            await tb.dispatch(ToolCall(name="spend_money", args={"amount": 1500}))

        # Never zero events: exactly one paired start + end.
        assert len(obs.starts) == 1
        assert len(obs.ends) == 1
        assert obs.order == ["start", "end"]
        assert obs.ends[0].status == "awaiting_approval"
        assert obs.ends[0].is_error is True
        # Paired by activity_id.
        assert obs.starts[0].activity_id == obs.ends[0].activity_id

    @pytest.mark.asyncio
    async def test_start_fires_before_the_inner_gate_raises(self) -> None:
        # Proves emit wraps the gate (not the reverse): the start is recorded even though
        # the inner dispatch raised before returning.
        obs = RecordingObserver()
        tb = ObservedToolbox(StubToolbox(raises=_GatedActionProposedError()), obs)
        with pytest.raises(_GatedActionProposedError):
            await tb.dispatch(ToolCall(name="external_mutate", args={}))
        assert obs.starts, "activity_start must fire before the gate raise (emit is outermost)"


# ---------------------------------------------------------------------------
# Section: ok / error / raise lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_ok_path_emits_paired_start_end(self) -> None:
        obs = RecordingObserver()
        tb = ObservedToolbox(Toolbox([_make_echo_tool("echo")], allow_list=["echo"]), obs)
        result = await tb.dispatch(ToolCall(name="echo", args={"text": "hi"}))
        assert result.content == "hi"
        assert len(obs.starts) == 1
        assert len(obs.ends) == 1
        assert obs.order == ["start", "end"]
        end = obs.ends[0]
        assert end.status == "ok"
        assert end.is_error is False
        assert end.duration_ms >= 0.0
        assert obs.starts[0].activity_id == end.activity_id
        assert obs.starts[0].kind == "tool"
        assert obs.starts[0].name == "echo"

    @pytest.mark.asyncio
    async def test_error_result_emits_error_end(self) -> None:
        obs = RecordingObserver()
        tb = ObservedToolbox(StubToolbox(is_error=True), obs)
        result = await tb.dispatch(ToolCall(name="boom", args={}))
        assert result.is_error is True
        assert obs.ends[0].status == "error"
        assert obs.ends[0].is_error is True

    @pytest.mark.asyncio
    async def test_not_allowed_raise_still_emits_end_then_reraises(self) -> None:
        # A raising inner (ToolNotAllowedError) must still produce a paired end.
        obs = RecordingObserver()
        tb = ObservedToolbox(Toolbox([_make_echo_tool("echo")], allow_list=["echo"]), obs)
        with pytest.raises(ToolNotAllowedError):
            await tb.dispatch(ToolCall(name="get_weather", args={"city": "Oslo"}))
        assert len(obs.starts) == 1
        assert len(obs.ends) == 1
        # A plain (non-carrier) raise classifies as error.
        assert obs.ends[0].status == "error"
        assert obs.ends[0].is_error is True


# ---------------------------------------------------------------------------
# Section: redaction at the emit boundary (the security gate, end-to-end)
# ---------------------------------------------------------------------------


class TestRedactionAtEmit:
    @pytest.mark.asyncio
    async def test_secrets_redacted_in_start_args_summary(self) -> None:
        obs = RecordingObserver()
        tb = ObservedToolbox(StubToolbox(), obs)
        await tb.dispatch(
            ToolCall(
                name="mcp:github:create_issue",
                args={"api_key": "sk-secret-123", "title": "bug", "blob": "Z" * 5000},
            )
        )
        summary = obs.starts[0].args_summary
        assert summary["api_key"] == "‹redacted›"
        assert "sk-secret-123" not in str(summary)
        assert "Z" * 5000 not in str(summary)
        assert summary["title"] == "bug"


# ---------------------------------------------------------------------------
# Section: kind derivation + step context
# ---------------------------------------------------------------------------


class TestKindAndContext:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("web_search", "web"),
            ("web_fetch", "web"),
            ("code_execution", "sandbox"),
            ("generate_image", "imagegen"),
            ("use_skill", "skill"),
            ("mcp:github:create_issue", "mcp"),
            ("render_diagram", "tool"),
            ("calculator", "tool"),
        ],
    )
    def test_activity_kind_for(self, name: str, expected: str) -> None:
        assert activity_kind_for(name) == expected

    @pytest.mark.asyncio
    async def test_step_context_threads_onto_events(self) -> None:
        obs = RecordingObserver()
        tb = ObservedToolbox(StubToolbox(), obs)
        await tb.dispatch(ToolCall(name="calculator", args={}), context=DispatchContext(step=4))
        assert obs.starts[0].step == 4
        assert obs.ends[0].step == 4

    @pytest.mark.asyncio
    async def test_default_step_is_run_level(self) -> None:
        obs = RecordingObserver()
        tb = ObservedToolbox(StubToolbox(), obs)
        await tb.dispatch(ToolCall(name="calculator", args={}))
        assert obs.starts[0].step == -1


# ---------------------------------------------------------------------------
# Section: instrumentation must never break a turn
# ---------------------------------------------------------------------------


class TestObserverIsolation:
    @pytest.mark.asyncio
    async def test_observer_start_failure_does_not_break_dispatch(self) -> None:
        class BrokenObserver:
            async def on_activity_start(self, start: ActivityStart) -> None:
                raise RuntimeError("observer boom")

            async def on_activity_end(self, end: ActivityEnd) -> None:
                raise RuntimeError("observer boom")

        tb = ObservedToolbox(StubToolbox(), BrokenObserver())
        # The tool result is returned despite the observer raising on both hooks.
        result = await tb.dispatch(ToolCall(name="calculator", args={}))
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_query_methods_forward_to_inner(self) -> None:
        inner = Toolbox([_make_echo_tool("echo")], allow_list=["echo"])
        tb = ObservedToolbox(inner, RecordingObserver())
        assert tb.is_allowed("echo") is True
        assert tb.names() == ["echo"]
        assert tb.kind_for("echo") == "builtin"
        assert len(tb.get_specs()) == 1
