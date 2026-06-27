"""Structural coverage for the P2 activity contract (T3) — the honesty guarantee.

Acceptance criterion 3: "no capability is silent — now or as phase 3 grows", enforced
**structurally at the single dispatch emit point** so a newly-added capability cannot ship
silent by omission. This file is that enforcement. It is deliberately precise about the
**scope** of the guarantee (your T2 note): the emit seam is ``on_event``-gated, so the
claim is "every *streamed* dispatch (sink present) emits a paired start+end", proven three
ways, plus an explicit enumeration of what is out-of-trail-scope.

1. :class:`TestKindUniformity` — every capability *kind* (and a novel, never-seen name)
   emits through the seam. Coverage is keyed off the seam, not an enumerated allowlist, so
   a new tool/MCP/skill is covered the moment it dispatches.
2. :class:`TestSingleSeamEnforcement` (source-level) — the runtime dispatches tools in
   exactly ONE place (``persona_runtime.activity``); neither loop calls
   ``toolbox.dispatch`` directly. A new in-loop path that bypasses the seam fails this test
   (the "ships silent → fails the build" guarantee).
3. :class:`TestOnEventScoping` — the seam is sink-gated by design; with a sink every
   dispatch emits, without one nothing does. The loops always thread the turn/run's
   ``on_event`` (so coverage is total *within* a streamed turn/run).
4. :class:`TestOutOfTrailScope` — the named exclusions (with rationale), tracked rather
   than silently uncovered.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import persona_runtime
import persona_runtime.activity as activity_mod
import persona_runtime.agentic.loop as agentic_loop_mod
import persona_runtime.loop as conv_loop_mod
import pytest
from persona.schema.tools import ToolCall, ToolResult
from persona.tools.activity import activity_kind_for
from persona_runtime.activity import dispatch_with_activity
from persona_runtime.agentic.events import RunEvent  # noqa: TC002 — used in helper signatures


class _OkToolbox:
    """A permissive dispatch target — returns ok for any call (no arg validation)."""

    async def dispatch(self, tool_call: ToolCall) -> ToolResult:
        return ToolResult(tool_name=tool_call.name, content="ok")

    def kind_for(self, tool_name: str) -> str:  # noqa: ARG002
        return "builtin"

    def get_specs(self) -> list[object]:
        return []

    def is_allowed(self, tool_name: str) -> bool:  # noqa: ARG002
        return True

    def names(self) -> list[str]:
        return []


async def _emit_for(name: str) -> list[RunEvent]:
    events: list[RunEvent] = []

    async def sink(ev: RunEvent) -> None:
        events.append(ev)

    await dispatch_with_activity(
        _OkToolbox(),  # type: ignore[arg-type]
        ToolCall(name=name, args={"x": 1}),
        on_event=sink,
        step=-1,
    )
    return events


# ---------------------------------------------------------------------------
# 1. Kind uniformity — no capability kind is special-cased, incl. novel names
# ---------------------------------------------------------------------------


class TestKindUniformity:
    @pytest.mark.parametrize(
        ("name", "expected_kind"),
        [
            ("web_search", "web"),
            ("web_fetch", "web"),
            ("code_execution", "sandbox"),
            ("generate_image", "imagegen"),  # the TOOL is covered (P2-D-imagegen)
            ("render_diagram", "tool"),
            ("text_summarize", "tool"),
            ("use_skill", "skill"),
            ("mcp:github:create_issue", "mcp"),
            ("calculator", "tool"),
            ("record_user_fact", "tool"),
            # A capability that does not exist yet — proves coverage is structural at the
            # seam, NOT an enumerated v1 list. A phase-3 tool emits the moment it dispatches.
            ("brand_new_capability_v99", "tool"),
        ],
    )
    @pytest.mark.asyncio
    async def test_every_kind_and_novel_name_emits_paired_events(
        self, name: str, expected_kind: str
    ) -> None:
        events = await _emit_for(name)
        assert [e.type for e in events] == ["activity_start", "activity_end"]
        assert events[0].data["kind"] == expected_kind
        assert events[0].data["name"] == name
        assert events[0].data["activity_id"] == events[1].data["activity_id"]
        # And the kind resolver agrees (single source of truth).
        assert activity_kind_for(name) == expected_kind


# ---------------------------------------------------------------------------
# 2. Single-seam enforcement (source-level) — no dispatch bypasses the seam
# ---------------------------------------------------------------------------


class TestSingleSeamEnforcement:
    def test_neither_loop_calls_toolbox_dispatch_directly(self) -> None:
        # Both loops must route tool dispatch through dispatch_with_activity; a bare
        # `self._toolbox.dispatch(` is the silent-path regression this guards against.
        for mod in (conv_loop_mod, agentic_loop_mod):
            src = inspect.getsource(mod)
            assert "dispatch_with_activity" in src, f"{mod.__name__} must use the activity seam"
            assert "_toolbox.dispatch(" not in src, (
                f"{mod.__name__} bypasses the activity seam with a direct toolbox.dispatch — "
                "every in-loop dispatch must go through dispatch_with_activity (P2 criterion 3)"
            )

    def test_runtime_dispatches_tools_in_exactly_one_module(self) -> None:
        # Walk the runtime source tree: the ONLY file that calls `<toolbox>.dispatch(` is
        # the seam. A new module that dispatches a tool elsewhere fails here.
        src_root = Path(persona_runtime.__file__).parent
        offenders = [
            path.relative_to(src_root).as_posix()
            for path in src_root.rglob("*.py")
            if ".dispatch(" in path.read_text(encoding="utf-8")
        ]
        assert offenders == ["activity.py"], (
            f"tool dispatch must live only in the activity seam; found in: {offenders}"
        )

    def test_seam_module_is_the_emit_point(self) -> None:
        src = inspect.getsource(activity_mod)
        assert "ObservedToolbox" in src
        assert ".dispatch(" in src  # the seam genuinely dispatches


# ---------------------------------------------------------------------------
# 3. on_event scoping — the guarantee is "streamed dispatches emit"
# ---------------------------------------------------------------------------


class TestOnEventScoping:
    @pytest.mark.asyncio
    async def test_with_sink_every_dispatch_emits(self) -> None:
        assert len(await _emit_for("calculator")) == 2  # start + end

    @pytest.mark.asyncio
    async def test_without_sink_nothing_emits_by_design(self) -> None:
        # The scoping boundary: a sink-less (non-streamed) dispatch emits nothing — there
        # is no RunEvent stream to emit to. In production the loops always thread the
        # turn/run's on_event (see test_loop_agentic / test_loop_mcp_telemetry integration
        # assertions), so coverage is total WITHIN a streamed turn/run.
        result = await dispatch_with_activity(
            _OkToolbox(),  # type: ignore[arg-type]
            ToolCall(name="calculator", args={}),
            on_event=None,
            step=-1,
        )
        assert result.content == "ok"  # dispatch still happens; just no events

    def test_both_loops_thread_on_event_into_the_seam(self) -> None:
        # The loops pass on_event through to dispatch_with_activity (not None-by-default),
        # so the in-turn/in-run guarantee holds. Asserted at the source so a refactor that
        # drops the threading is caught.
        for mod in (conv_loop_mod, agentic_loop_mod):
            src = inspect.getsource(mod)
            assert "dispatch_with_activity(" in src
            assert "on_event=on_event" in src, (
                f"{mod.__name__} must thread on_event into the seam so every in-run dispatch emits"
            )


# ---------------------------------------------------------------------------
# 4. Out-of-trail-scope — the named exclusions, tracked not silent
# ---------------------------------------------------------------------------

#: Dispatches deliberately NOT on the activity trail in this spec, each with its reason.
#: Tracked here (not silently uncovered) so a future change that should cover one of these
#: has a single place to reconcile — the honesty the structural claim depends on.
_OUT_OF_TRAIL_SCOPE = {
    "imagegen_http_route": (
        "POST /v1/personas/{id}/imagegen is a USER-INITIATED route, not a mid-turn "
        "dispatch — the persona is not deciding to act. The generate_image TOOL (which the "
        "model calls mid-turn) IS covered via the seam; the route is not (P2-D-imagegen)."
    ),
    "voice_reply_producer_dispatch": (
        "packages/voice reply_producer.py calls toolbox.dispatch directly with no on_event "
        "sink today — forward-coverage (V10/T6): it lights up when its toolbox is wrapped "
        "/ a voice activity sink is wired. Named, not silent."
    ),
    "non_streamed_dispatch": (
        "A dispatch with on_event=None (a non-streaming caller) emits nothing by design — "
        "no RunEvent stream exists. Not a gap: production turns/runs always stream."
    ),
}


class TestOutOfTrailScope:
    def test_exclusions_are_enumerated_with_rationale(self) -> None:
        assert set(_OUT_OF_TRAIL_SCOPE) == {
            "imagegen_http_route",
            "voice_reply_producer_dispatch",
            "non_streamed_dispatch",
        }
        for reason in _OUT_OF_TRAIL_SCOPE.values():
            assert len(reason) > 40  # a real rationale, not a placeholder

    @pytest.mark.asyncio
    async def test_generate_image_tool_is_covered_not_excluded(self) -> None:
        # The exclusion is the HTTP route only; the mid-turn TOOL is covered.
        assert activity_kind_for("generate_image") == "imagegen"
        events = await _emit_for("generate_image")
        assert [e.type for e in events] == ["activity_start", "activity_end"]
