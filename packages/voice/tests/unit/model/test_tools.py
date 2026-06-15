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
        policy = VoiceToolPolicy()
        assert policy.classify("web_search") is VoiceToolDisposition.VOICE_VIABLE
        assert policy.classify("web_fetch") is VoiceToolDisposition.VOICE_VIABLE
        assert policy.classify("code_execution") is VoiceToolDisposition.DEFERRED
        assert policy.classify("document_generation") is VoiceToolDisposition.DEFERRED
        assert policy.classify("calculator") is VoiceToolDisposition.TEXT_ONLY

    def test_offered_specs_excludes_text_only_keeps_viable_and_deferred(self) -> None:
        policy = VoiceToolPolicy()
        specs = [_spec("web_search"), _spec("code_execution"), _spec("calculator")]
        offered = {s.name for s in policy.offered_specs(specs)}
        assert offered == {"web_search", "code_execution"}  # calculator (text-only) withheld

    def test_custom_sets(self) -> None:
        policy = VoiceToolPolicy(voice_viable=frozenset({"calculator"}), deferred=frozenset())
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
