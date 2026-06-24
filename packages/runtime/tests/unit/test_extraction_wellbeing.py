"""Wellbeing-category tagging + means-redaction verification (Spec K2, T5; criterion 7).

The tagging mechanism shipped in T2 (prompt rule + parser map) and T4 (prompt v2).
T5 is the FOCUSED structural verification through the full ``LlmExtractor`` pipeline
(mocked LLM — T6 is the real-model MEASURED hard gate):

- each K4 category, when the model discloses it, rides through to the candidate's
  ``WellbeingCategory`` (criterion 7 — an untagged-sensitive write is a failure);
- the over-tagging guard: ordinary health / small talk is NOT tagged sensitive
  (over-tagging is itself a failure — it would make K4 over-gate);
- the means-redaction slice (D-K2-7): the self-harm struggle is tagged ``self_harm``
  and the method/means token appears in ZERO candidate fields.
"""

from __future__ import annotations

from typing import Any

import pytest
from persona.backends.types import ChatResponse, TokenUsage
from persona.extraction import ExtractionInput, InteractionKind
from persona.wellbeing import WellbeingCategory
from persona_runtime.extraction.pipeline import LlmExtractor
from persona_runtime.extraction.prompt import EXAMPLE_MEANS_REDACTION_OUTPUT


class _StubBackend:
    """Returns a fixed JSON ``content`` as the model's response."""

    def __init__(self, content: str) -> None:
        self._content = content

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
        return ChatResponse(
            content=self._content,
            tool_calls=[],
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model=self.model_name,
            provider=self.provider_name,
            latency_ms=0.0,
        )

    def chat_stream(self, *a: Any, **k: Any) -> Any:  # noqa: ANN401 - unused
        raise NotImplementedError


def _interaction() -> ExtractionInput:
    return ExtractionInput(
        interaction_kind=InteractionKind.CONVERSATION,
        interaction_id="c1",
        persona_id="p1",
        content="(interaction text)",
    )


def _one_candidate(*, category: str | None) -> str:
    cat = "null" if category is None else f'"{category}"'
    return (
        '{"candidates": [{"concept_name": "disclosure", "content": "a grounded disclosure",'
        ' "node_kind": "circumstance", "evidence_span": "the user said it",'
        f' "wellbeing_category": {cat}}}]}}'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("category", list(WellbeingCategory))
async def test_each_k4_category_is_tagged_through_the_pipeline(category: WellbeingCategory) -> None:
    backend = _StubBackend(_one_candidate(category=category.value))
    out = await LlmExtractor(backend=backend).extract(_interaction())
    assert out[0].wellbeing_category is category  # criterion 7: the tag is carried


@pytest.mark.asyncio
async def test_ordinary_health_is_not_tagged_sensitive() -> None:
    # The medication-budget case: ordinary health flows UNTAGGED (must reach every
    # persona). Over-tagging would make K4 over-gate — it is itself a failure.
    backend = _StubBackend(
        '{"candidates": [{"concept_name": "blood pressure medication",'
        ' "content": "The user takes medication for high blood pressure.",'
        ' "node_kind": "circumstance", "evidence_span": "I take meds for my blood pressure",'
        ' "wellbeing_category": null}]}'
    )
    out = await LlmExtractor(backend=backend).extract(_interaction())
    assert out[0].wellbeing_category is None


@pytest.mark.asyncio
async def test_small_talk_yields_no_tagged_candidates() -> None:
    out = await LlmExtractor(backend=_StubBackend('{"candidates": []}')).extract(_interaction())
    assert out == ()


@pytest.mark.asyncio
async def test_the_pipeline_never_injects_a_tag_the_model_did_not_emit() -> None:
    # Structural guarantee: a candidate with no category stays untagged end-to-end.
    backend = _StubBackend(_one_candidate(category=None))
    out = await LlmExtractor(backend=backend).extract(_interaction())
    assert out[0].wellbeing_category is None


@pytest.mark.asyncio
async def test_means_redaction_slice_tags_self_harm_and_emits_zero_means(  # D-K2-7
) -> None:
    # The model output redacts the means; the pipeline carries the struggle tagged
    # self_harm with the method/means token in NO candidate field (hard gate).
    backend = _StubBackend(EXAMPLE_MEANS_REDACTION_OUTPUT)
    out = await LlmExtractor(backend=backend).extract(_interaction())
    assert any(c.wellbeing_category is WellbeingCategory.SELF_HARM for c in out)
    fields = " ".join(c.concept_name + " " + c.content + " " + c.evidence_span for c in out).lower()
    assert "pills" not in fields
    assert "overdose" not in fields
