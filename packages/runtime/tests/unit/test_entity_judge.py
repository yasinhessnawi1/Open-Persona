"""The AMBIGUOUS-band entity judge (Spec K2, T8c; D-K0-9).

K0's registry resolves the confident cases deterministically and hands K2 only the
ambiguous review band. This is K2's LLM judge for that band: a single strict binary
"same real-world entity?" call over the candidates, conservative by design (prefer
a NEW entity on any doubt — a wrong merge fuses two real people). Mocked-backend
unit tests; the real-tier behaviour is validated at T8d.
"""

from __future__ import annotations

from typing import Any

import pytest
from persona.backends.types import ChatResponse, TokenUsage
from persona.extraction import EntityRecognizer
from persona.graph.protocol import EntityCandidate
from persona_runtime.extraction.entity_judge import LlmEntityJudge


class _StubBackend:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return "claude-haiku-4-5-20251001"

    @property
    def supports_native_tools(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False

    async def chat(self, messages: object, **kwargs: Any) -> ChatResponse:  # noqa: ANN401, ARG002
        self.calls += 1
        return ChatResponse(
            content=self._content,
            tool_calls=[],
            usage=TokenUsage(prompt_tokens=5, completion_tokens=1, total_tokens=6),
            model=self.model_name,
            provider=self.provider_name,
            latency_ms=0.0,
        )

    def chat_stream(self, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        raise NotImplementedError


_CANDS = (
    EntityCandidate(entity_id="e1", canonical_name="Dr. Hansen", score=0.86),
    EntityCandidate(entity_id="e2", canonical_name="Dr. Hauge", score=0.81),
)


def test_implements_the_entity_recognizer_protocol() -> None:
    assert isinstance(LlmEntityJudge(backend=_StubBackend("NONE")), EntityRecognizer)


@pytest.mark.asyncio
async def test_no_candidates_returns_none_without_a_model_call() -> None:
    backend = _StubBackend("1")
    out = await LlmEntityJudge(backend=backend).judge_ambiguous(
        owner_id="u1", mention="my doctor", candidates=()
    )
    assert out is None
    assert backend.calls == 0


@pytest.mark.asyncio
async def test_a_chosen_number_maps_to_that_candidate_id() -> None:
    out = await LlmEntityJudge(backend=_StubBackend("1")).judge_ambiguous(
        owner_id="u1", mention="my doctor", candidates=_CANDS
    )
    assert out == "e1"


@pytest.mark.asyncio
async def test_second_candidate() -> None:
    out = await LlmEntityJudge(backend=_StubBackend("the answer is 2")).judge_ambiguous(
        owner_id="u1", mention="the GP", candidates=_CANDS
    )
    assert out == "e2"


@pytest.mark.asyncio
async def test_none_answer_returns_none() -> None:
    out = await LlmEntityJudge(backend=_StubBackend("NONE")).judge_ambiguous(
        owner_id="u1", mention="someone else", candidates=_CANDS
    )
    assert out is None


@pytest.mark.asyncio
async def test_out_of_range_or_garbage_is_conservative_none() -> None:
    # Out of range or unparseable → the safe SEPARATE (a wrong merge is catastrophic).
    assert (
        await LlmEntityJudge(backend=_StubBackend("9")).judge_ambiguous(
            owner_id="u1", mention="x", candidates=_CANDS
        )
        is None
    )
    assert (
        await LlmEntityJudge(backend=_StubBackend("hmm not sure")).judge_ambiguous(
            owner_id="u1", mention="x", candidates=_CANDS
        )
        is None
    )
