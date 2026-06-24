"""The extraction ports — ``Extractor`` / ``EntityRecognizer`` (Spec K2, T1).

Both Protocols are persona-CORE contracts implemented in persona-RUNTIME (where
the LLM + the Spec-18 router live; core stays LLM-free per K0). The runtime
extractor produces ``ExtractionCandidate``s; the recognizer is K2's LLM judge on
K0's AMBIGUOUS resolution band ONLY (K0 owns the deterministic verdict, D-K0-9).
These tests pin the runtime-checkable surface so a conforming async impl is
recognised and a non-conforming object is not.
"""

# ruff: noqa: ARG002 — protocol-conformance stubs deliberately ignore their args

from __future__ import annotations

import pytest
from persona.extraction import (
    EntityRecognizer,
    ExtractionCandidate,
    ExtractionInput,
    Extractor,
    InteractionKind,
)
from persona.graph.models import NodeKind
from persona.graph.protocol import EntityCandidate


class _ConformingExtractor:
    async def extract(self, interaction: ExtractionInput) -> tuple[ExtractionCandidate, ...]:
        return (
            ExtractionCandidate(
                concept_name="c",
                content="d",
                node_kind=NodeKind.FACT,
                evidence_span="said so",
            ),
        )


class _ConformingRecognizer:
    async def judge_ambiguous(
        self, *, owner_id: str, mention: str, candidates: tuple[EntityCandidate, ...]
    ) -> str | None:
        return candidates[0].entity_id if candidates else None


class _NotAnExtractor:
    def something_else(self) -> None: ...


def test_extractor_is_runtime_checkable() -> None:
    assert isinstance(_ConformingExtractor(), Extractor)
    assert not isinstance(_NotAnExtractor(), Extractor)


def test_entity_recognizer_is_runtime_checkable() -> None:
    assert isinstance(_ConformingRecognizer(), EntityRecognizer)
    assert not isinstance(_NotAnExtractor(), EntityRecognizer)


@pytest.mark.asyncio
async def test_extractor_returns_candidates() -> None:
    out = await _ConformingExtractor().extract(
        ExtractionInput(
            interaction_kind=InteractionKind.CONVERSATION,
            interaction_id="c1",
            persona_id="p1",
            content="text",
        )
    )
    assert out[0].concept_name == "c"


@pytest.mark.asyncio
async def test_recognizer_judges_the_ambiguous_band() -> None:
    cands = (EntityCandidate(entity_id="e1", canonical_name="Dr. Hansen", score=0.85),)
    chosen = await _ConformingRecognizer().judge_ambiguous(
        owner_id="u1", mention="my doctor", candidates=cands
    )
    assert chosen == "e1"
    none_chosen = await _ConformingRecognizer().judge_ambiguous(
        owner_id="u1", mention="someone new", candidates=()
    )
    assert none_chosen is None
