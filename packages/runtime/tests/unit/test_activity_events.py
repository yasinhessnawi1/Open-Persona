"""Tests for the P2 activity contract in the runtime (T2).

Covers the ``RunEvent.activity_start``/``activity_end`` constructors, the
``RunEventActivityObserver`` adapter (core ``ActivityStart``/``ActivityEnd`` → RunEvent),
and the ``dispatch_with_activity`` seam (bare when no sink; ObservedToolbox-wrapped —
emit outermost — when a sink is present, incl. the gated-raise path).
"""

# ruff: noqa: ARG002 — stub methods accept unused protocol args by design
from __future__ import annotations

import pytest
from persona.schema.tools import ToolCall, ToolResult
from persona.tools.activity import ActivityEnd, ActivityStart, ActivityStatus
from persona.tools.protocol import tool
from persona.tools.toolbox import Toolbox
from persona_runtime.activity import RunEventActivityObserver, dispatch_with_activity
from persona_runtime.agentic.events import RunEvent


@tool(name="echo", description="Echo a message back.")
async def _echo(message: str) -> ToolResult:
    return ToolResult(tool_name="echo", content=f"echoed: {message}")


class _GatedActionProposedError(Exception):
    """Stub of A3's gate exception (declares the awaiting-approval status)."""

    @property
    def activity_status(self) -> ActivityStatus:
        return "awaiting_approval"


class _RaisingToolbox:
    """A dispatch target that raises a gate exception (A3 stand-in)."""

    async def dispatch(self, tool_call: ToolCall) -> ToolResult:
        raise _GatedActionProposedError

    def kind_for(self, tool_name: str) -> str:
        return "builtin"

    def get_specs(self) -> list[object]:
        return []

    def is_allowed(self, tool_name: str) -> bool:
        return True

    def names(self) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Section: RunEvent constructors
# ---------------------------------------------------------------------------


class TestActivityConstructors:
    def test_activity_start_payload(self) -> None:
        ev = RunEvent.activity_start(
            -1,
            activity_id="a1",
            kind="web",
            name="web_search",
            label="Searching the web",
            args_summary={"query": "oslo rent"},
        )
        assert ev.type == "activity_start"
        assert ev.step == -1
        assert ev.data["activity_id"] == "a1"
        assert ev.data["kind"] == "web"
        assert ev.data["name"] == "web_search"
        assert ev.data["label"] == "Searching the web"
        assert ev.data["args_summary"] == {"query": "oslo rent"}

    def test_activity_end_payload_ok(self) -> None:
        ev = RunEvent.activity_end(
            3,
            activity_id="a1",
            status="ok",
            duration_ms=12.5,
            is_error=False,
            result_summary="done",
        )
        assert ev.type == "activity_end"
        assert ev.step == 3
        assert ev.data["status"] == "ok"
        assert ev.data["is_error"] is False
        assert ev.data["duration_ms"] == 12.5
        assert ev.data["result_summary"] == "done"

    def test_activity_end_awaiting_approval(self) -> None:
        ev = RunEvent.activity_end(
            -1, activity_id="a1", status="awaiting_approval", duration_ms=1.0, is_error=True
        )
        assert ev.data["status"] == "awaiting_approval"
        assert ev.data["is_error"] is True
        # Empty result_summary is omitted (back-compat-shaped payload).
        assert "result_summary" not in ev.data

    def test_activity_events_are_json_safe(self) -> None:
        # The events cross the SSE boundary — model_dump_json must succeed.
        start = RunEvent.activity_start(
            -1, activity_id="a", kind="tool", name="x", label="Using x", args_summary={"k": "v"}
        )
        end = RunEvent.activity_end(
            -1, activity_id="a", status="ok", duration_ms=1.0, is_error=False
        )
        assert "activity_start" in start.model_dump_json()
        assert "activity_end" in end.model_dump_json()


# ---------------------------------------------------------------------------
# Section: the RunEvent adapter
# ---------------------------------------------------------------------------


class TestRunEventActivityObserver:
    @pytest.mark.asyncio
    async def test_translates_start_and_end(self) -> None:
        events: list[RunEvent] = []

        async def sink(ev: RunEvent) -> None:
            events.append(ev)

        adapter = RunEventActivityObserver(sink)
        await adapter.on_activity_start(
            ActivityStart(activity_id="a1", kind="mcp", name="mcp:gh:x", label="Using gh", step=2)
        )
        await adapter.on_activity_end(
            ActivityEnd(activity_id="a1", status="ok", duration_ms=5.0, is_error=False, step=2)
        )
        assert [e.type for e in events] == ["activity_start", "activity_end"]
        assert events[0].data["activity_id"] == "a1"
        assert events[0].step == 2
        assert events[1].data["status"] == "ok"


# ---------------------------------------------------------------------------
# Section: dispatch_with_activity — the loop seam
# ---------------------------------------------------------------------------


class TestDispatchWithActivity:
    @pytest.mark.asyncio
    async def test_no_sink_dispatches_bare_with_no_events(self) -> None:
        tb = Toolbox([_echo], allow_list=["echo"])
        result = await dispatch_with_activity(
            tb, ToolCall(name="echo", args={"message": "hi"}), on_event=None, step=-1
        )
        assert result.content == "echoed: hi"

    @pytest.mark.asyncio
    async def test_sink_emits_paired_activity_events(self) -> None:
        events: list[RunEvent] = []

        async def sink(ev: RunEvent) -> None:
            events.append(ev)

        tb = Toolbox([_echo], allow_list=["echo"])
        result = await dispatch_with_activity(
            tb, ToolCall(name="echo", args={"message": "hi"}), on_event=sink, step=7
        )
        assert result.content == "echoed: hi"
        assert [e.type for e in events] == ["activity_start", "activity_end"]
        assert events[0].step == 7
        assert events[0].data["name"] == "echo"
        assert events[1].data["status"] == "ok"
        # Paired by activity_id.
        assert events[0].data["activity_id"] == events[1].data["activity_id"]

    @pytest.mark.asyncio
    async def test_gated_raise_emits_start_then_awaiting_approval_then_reraises(self) -> None:
        # P2-D-a3-composition end-to-end: emit is outermost, so a gate-raise still
        # produces start + awaiting_approval (never zero) before propagating.
        events: list[RunEvent] = []

        async def sink(ev: RunEvent) -> None:
            events.append(ev)

        with pytest.raises(_GatedActionProposedError):
            await dispatch_with_activity(
                _RaisingToolbox(),  # type: ignore[arg-type]
                ToolCall(name="spend_money", args={"amount": 1500}),
                on_event=sink,
                step=-1,
            )
        assert [e.type for e in events] == ["activity_start", "activity_end"]
        assert events[1].data["status"] == "awaiting_approval"
