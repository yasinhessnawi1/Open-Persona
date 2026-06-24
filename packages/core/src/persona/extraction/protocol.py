"""The extraction ports — ``Extractor`` / ``EntityRecognizer`` (Spec K2, T1).

Persona-CORE contracts implemented in persona-RUNTIME (the ratified layering
split): the runtime extractor calls the Spec-18 router / an LLM to produce
candidates; the recognizer is K2's LLM judge on K0's AMBIGUOUS resolution band
only. Core declares the shapes and stays LLM-free (K0's discipline); the runtime
injects the concrete implementations (dependency inversion). Both are
``@runtime_checkable`` so composition roots can assert conformance.

The methods are ``async`` because their runtime implementations make model calls
(the synthesis handler and the agentic loop are async); the contract lives in
core but is a contract for a runtime component, not a core store (K0's
``GraphStore`` stays synchronous, D-07-1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from persona.extraction.models import ExtractionCandidate, ExtractionInput
    from persona.graph.protocol import EntityCandidate

__all__ = ["EntityRecognizer", "Extractor"]


@runtime_checkable
class Extractor(Protocol):
    """Turns a completed interaction into grounded knowledge candidates (K2 §2/§4).

    The heart of K2. The runtime implementation encodes the §4 judgement rules in
    a versioned prompt (D-K2-3) and routes on the small/mid tier (D-K2-3); it
    returns a *handful* of grounded candidates from a rich interaction and
    near-zero from small talk (the restraint discipline, criterion 6). Each
    returned candidate carries a non-empty ``evidence_span`` by construction.
    """

    async def extract(self, interaction: ExtractionInput) -> tuple[ExtractionCandidate, ...]:
        """Extract grounded candidates from ``interaction`` (empty tuple if none)."""
        ...


@runtime_checkable
class EntityRecognizer(Protocol):
    """K2's LLM judge on K0's AMBIGUOUS resolution band only (D-K0-9).

    K0's ``EntityRegistry.resolve`` returns a deterministic, LLM-free three-way
    verdict; the wide review band returns ``AMBIGUOUS`` with candidates. K2 owns
    the single strict binary judgement on that band — never on ``MERGE`` /
    ``SEPARATE`` (those are K0's deterministic decisions). The orchestrator then
    calls ``add_alias`` (chosen) or ``create_entity`` (none chosen).
    """

    async def judge_ambiguous(
        self,
        *,
        owner_id: str,
        mention: str,
        candidates: tuple[EntityCandidate, ...],
    ) -> str | None:
        """Return the chosen canonical ``entity_id``, or ``None`` to create a new one."""
        ...
