"""Unit tests for the voice-tools design units (spec V5 T7; D-V5-4/5).

Policy partition, the preamble-as-contract narrator, the hard latency bound, and
the F5 deferred-artifact intent record.
"""

from __future__ import annotations

import asyncio

import pytest
from persona.backends.types import ToolSpec
from persona.schema.tools import ToolResult
from persona_voice.model import (
    BoundedToolOutcome,
    DeferredArtifact,
    VoiceToolDisposition,
    VoiceToolNarrator,
    VoiceToolPolicy,
    run_tool_with_latency_bound,
)
from pydantic import ValidationError


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description=f"{name} tool", parameters={"type": "object"})


class TestVoiceToolPolicy:
    def test_default_classification(self) -> None:
        """V10-D-1 — partition by MEASURED latency, not 'visual vs text'."""
        policy = VoiceToolPolicy()
        # Inline-fast (run live under the latency bound): search + the sub-100ms
        # diagram (persists mermaid/graphviz source; the browser renders the SVG).
        assert policy.classify("web_search") is VoiceToolDisposition.VOICE_VIABLE
        assert policy.classify("web_fetch") is VoiceToolDisposition.VOICE_VIABLE
        assert policy.classify("render_diagram") is VoiceToolDisposition.VOICE_VIABLE
        # Async-slow + artifact: the 5–20s image gen → the render-when-ready lane.
        assert policy.classify("generate_image") is VoiceToolDisposition.ASYNC_ARTIFACT
        # Deferred (heavy / write — acknowledged off the live path).
        assert policy.classify("code_execution") is VoiceToolDisposition.DEFERRED
        assert policy.classify("file_write") is VoiceToolDisposition.DEFERRED
        # Out of voice's cut: document_generation has no backend on main → not
        # offered; arbitrary builtins stay text-only.
        assert policy.classify("document_generation") is VoiceToolDisposition.TEXT_ONLY
        assert policy.classify("calculator") is VoiceToolDisposition.TEXT_ONLY

    def test_offered_specs_includes_async_artifact_excludes_text_only(self) -> None:
        policy = VoiceToolPolicy()
        specs = [
            _spec("web_search"),
            _spec("render_diagram"),
            _spec("generate_image"),
            _spec("code_execution"),
            _spec("document_generation"),
            _spec("calculator"),
        ]
        offered = {s.name for s in policy.offered_specs(specs)}
        # viable + async_artifact + deferred are offered; text-only withheld
        # (doc-gen has no backend, calculator is not a voice capability).
        assert offered == {"web_search", "render_diagram", "generate_image", "code_execution"}

    def test_generate_image_is_reachable_not_silently_dropped(self) -> None:
        """Regression: the V5 deferred set named the dead string 'image_generation'.

        The real tool is ``generate_image`` ([imagegen/tool.py] ``@tool``), so image
        generation was classified TEXT_ONLY and silently never offered in voice. It
        must now be reachable (V10-D-1).
        """
        policy = VoiceToolPolicy()
        assert policy.classify("generate_image") is not VoiceToolDisposition.TEXT_ONLY
        assert "generate_image" in {s.name for s in policy.offered_specs([_spec("generate_image")])}

    def test_custom_sets(self) -> None:
        policy = VoiceToolPolicy(
            voice_viable=frozenset({"calculator"}),
            deferred=frozenset(),
            async_artifact=frozenset(),
        )
        assert policy.classify("calculator") is VoiceToolDisposition.VOICE_VIABLE
        assert policy.classify("web_search") is VoiceToolDisposition.TEXT_ONLY


class TestVoiceToolNarrator:
    def test_preamble_rotates_by_index(self) -> None:
        narrator = VoiceToolNarrator(preambles=("one", "two", "three"))
        assert narrator.preamble(index=0) == "one"
        assert narrator.preamble(index=1) == "two"
        assert narrator.preamble(index=3) == "one"  # wraps → varied across turns

    def test_empty_preambles_raises(self) -> None:
        with pytest.raises(ValueError, match="preambles"):
            VoiceToolNarrator(preambles=())

    def test_async_artifact_line_signals_on_screen_and_is_distinct(self) -> None:
        """V10-D-3 — the inline acknowledgement spoken when a slow visual artifact
        is handed to the off-turn lane (distinct from the deferred-heavy line)."""
        narrator = VoiceToolNarrator()
        assert "screen" in narrator.async_artifact_line.lower()
        assert narrator.async_artifact_line != narrator.deferral_line

    def test_deferral_and_overflow_lines(self) -> None:
        narrator = VoiceToolNarrator(deferral_line="later", overflow_line="a moment")
        assert narrator.deferral_line == "later"
        assert narrator.overflow_line == "a moment"


class TestLatencyBound:
    @pytest.mark.asyncio
    async def test_fast_tool_returns_result(self) -> None:
        async def fast() -> ToolResult:
            return ToolResult(tool_name="web_search", content="done")

        outcome = await run_tool_with_latency_bound(fast(), timeout_s=1.0)
        assert outcome.timed_out is False
        assert outcome.result is not None
        assert outcome.result.content == "done"

    @pytest.mark.asyncio
    async def test_slow_tool_times_out_gracefully(self) -> None:
        async def slow() -> ToolResult:
            await asyncio.sleep(1.0)
            return ToolResult(tool_name="web_search", content="late")

        outcome = await run_tool_with_latency_bound(slow(), timeout_s=0.01)
        assert outcome == BoundedToolOutcome(result=None, timed_out=True)


class TestDeferredArtifact:
    def test_is_frozen_intent_record(self) -> None:
        artifact = DeferredArtifact(
            tool_name="document_generation",
            arguments={"format": "pdf"},
            spoken_acknowledgement="I'll prepare that.",
        )
        assert artifact.tool_name == "document_generation"
        assert artifact.arguments == {"format": "pdf"}
        with pytest.raises(ValidationError):
            artifact.tool_name = "x"  # type: ignore[misc]
