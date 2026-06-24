"""Knowledge extraction contracts (Spec K2 — the write paths' core layer).

The persona-CORE half of K2: the frozen, LLM-free boundary shapes and ports the
runtime extraction pipeline (the LLM parsing → entity recognition → candidate
knowledge) is built against. The runtime owns the LLM implementation; core owns
the contract (the ratified layering split). Candidates flow on to K0's
``GraphStore.merge`` via the K2 orchestrator.
"""

from __future__ import annotations

from persona.extraction.models import (
    EntityMention,
    ExtractionCandidate,
    ExtractionInput,
    InteractionKind,
    ProposedRelation,
)
from persona.extraction.protocol import EntityRecognizer, Extractor

__all__ = [
    "EntityMention",
    "EntityRecognizer",
    "ExtractionCandidate",
    "ExtractionInput",
    "Extractor",
    "InteractionKind",
    "ProposedRelation",
]
