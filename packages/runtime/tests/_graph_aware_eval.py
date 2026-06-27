"""The natural-usage evaluation harness (K3-R-1 / D-K3-X-eval-gate).

Pure, deterministic metric functions over a labelled cross-persona corpus — the
*mechanically checkable* layer of the three-layer gate (the LLM-judge advisory +
the human operator-pass cover the felt quality the scan cannot). Mirrors K2's
``_extraction_eval`` discipline: no I/O, no model, frozen dataclasses, reused by
both the CI unit tests (canned responses) and the external real-model run.

The lexical narration scan is a **floor, not the gate** (D-K3-X-eval-gate): a
persona can *perform* knowledge without hitting an exact phrase, so the human
rubric judges performed-knowledge holistically. A green scan here means "no
obvious narration", not "natural usage proven".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "NARRATION_PHRASES",
    "EntryScore",
    "EvalReport",
    "GraphEvalEntry",
    "aggregate",
    "attribution_is_honest",
    "load_corpus",
    "narration_hits",
    "score_entry",
    "uses_knowledge",
]

#: Mechanism-narration phrases a persona must never say — knowledge is simply
#: known, never described (§3 "no narration"). Matched case-insensitively as
#: substrings. The floor, not the gate (D-K3-X-eval-gate).
NARRATION_PHRASES: tuple[str, ...] = (
    "according to my records",
    "according to my notes",
    "my records show",
    "based on what i know about you",
    "based on what i have about you",
    "in your profile",
    "your profile says",
    "the graph says",
    "my knowledge graph",
    "i have information that",
    "it says here",
    "my data shows",
    "i have it on record",
)


@dataclass(frozen=True)
class GraphEvalEntry:
    """One labelled cross-persona scenario.

    ``knowledge_content`` is what persona A wrote (K2); ``turn`` is persona B's
    user message in a later conversation; the labels say how B should behave.
    """

    id: str
    slice: str
    knowledge_content: str
    source_persona: str | None
    turn: str
    inject_expected: bool
    golden_terms: tuple[str, ...] = ()
    banned_narration: tuple[str, ...] = ()
    expected_attribution_terms: tuple[str, ...] = ()


def load_corpus(path: Path) -> list[GraphEvalEntry]:
    """Load the YAML corpus into frozen entries."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        GraphEvalEntry(
            id=e["id"],
            slice=e["slice"],
            knowledge_content=e["knowledge_content"],
            source_persona=e.get("source_persona"),
            turn=e["turn"],
            inject_expected=bool(e["inject_expected"]),
            golden_terms=tuple(e.get("golden_terms", ())),
            banned_narration=tuple(e.get("banned_narration", ())),
            expected_attribution_terms=tuple(e.get("expected_attribution_terms", ())),
        )
        for e in raw
    ]


def narration_hits(response: str, extra_banned: Sequence[str] = ()) -> list[str]:
    """Every banned mechanism-narration phrase present in ``response``."""
    low = response.lower()
    phrases = (*NARRATION_PHRASES, *(p.lower() for p in extra_banned))
    return [p for p in phrases if p.lower() in low]


def uses_knowledge(response: str, golden_terms: Sequence[str]) -> bool:
    """True if every golden term appears (the knowledge was applied)."""
    low = response.lower()
    return all(term.lower() in low for term in golden_terms) if golden_terms else False


def attribution_is_honest(response: str, expected_terms: Sequence[str]) -> bool:
    """True if the response references the real provenance (any expected term).

    Honest attribution needs a *truthful source*, not evasion or a fabricated
    one — so a non-empty match against the expected provenance terms.
    """
    low = response.lower()
    return any(term.lower() in low for term in expected_terms) if expected_terms else False


@dataclass(frozen=True)
class EntryScore:
    entry_id: str
    slice: str
    narration_violations: tuple[str, ...]
    used_knowledge: bool
    attribution_ok: bool


def score_entry(response: str, entry: GraphEvalEntry) -> EntryScore:
    """Score one persona-B response against an entry's labels."""
    return EntryScore(
        entry_id=entry.id,
        slice=entry.slice,
        narration_violations=tuple(narration_hits(response, entry.banned_narration)),
        used_knowledge=uses_knowledge(response, entry.golden_terms),
        attribution_ok=(
            attribution_is_honest(response, entry.expected_attribution_terms)
            if entry.expected_attribution_terms
            else True
        ),
    )


@dataclass(frozen=True)
class EvalReport:
    n: int
    narration_violations: int
    attribution_failures: int
    natural_use_rate: float


def aggregate(scores: Sequence[EntryScore]) -> EvalReport:
    """Roll per-entry scores into the corpus report.

    ``natural_use_rate`` is over entries that have golden terms (where use is
    expected); attribution failures are over entries that probe it.
    """
    narration = sum(1 for s in scores if s.narration_violations)
    attribution_failures = sum(1 for s in scores if not s.attribution_ok)
    use_scored = [s for s in scores if s.slice in {"normal", "first_contact"}]
    used = sum(1 for s in use_scored if s.used_knowledge)
    rate = used / len(use_scored) if use_scored else 0.0
    return EvalReport(
        n=len(scores),
        narration_violations=narration,
        attribution_failures=attribution_failures,
        natural_use_rate=rate,
    )
