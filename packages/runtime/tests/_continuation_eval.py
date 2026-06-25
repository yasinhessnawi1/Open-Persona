"""The continuation evaluation harness (Spec A2, T11; A2-R-2 / D-A2-X-eval-gate).

A2's analogue of K2/K3's natural-usage gate — the **judged** proof that a task continues
coherently across legs rather than failing as **amnesia** (re-deriving an established
conclusion) or **ossification** (executing a plan a freshly-learned fact invalidated).

Like the K2 extraction eval, the **model-free pieces live here** — the committed scenario
loader, the rubric→verdict logic, the self-consistency vote, and the gate — so they are
unit-tested on **canned judge output** (no model), while the real LLM judge runs
``@pytest.mark.external``. The judge is **pointwise + reference-guided** (handed the planted
Established-Conclusion + Injected-Fresh-Fact as the answer key) and from a **different model
family** than the agent (self-enhancement bias).

The **honesty clause** is the load-bearing bit: a leg is labelled OSSIFICATION **only when the
fresh fact was provably present** in the reconstructed context (``fresh_fact_present``). If F
never reached the context, that is a retrieval/checkpoint *plumbing* bug → ``INCONCLUSIVE``,
not a reasoning failure — so the gate cannot false-fail (or false-pass) on plumbing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import yaml

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "ContinuationJudge",
    "EvalReport",
    "RubricScores",
    "Scenario",
    "Verdict",
    "gate",
    "load_scenarios",
    "majority_verdict",
    "verdict_for",
]


class Verdict(StrEnum):
    """The three labels (+ INCONCLUSIVE for borderline / plumbing — routes to human review)."""

    COHERENT = "coherent"
    AMNESIA = "amnesia"
    OSSIFICATION = "ossification"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Scenario:
    """One multi-leg mission with its two planted artifacts (the references)."""

    id: str
    mission: str  # research | monitoring | preparation
    contract_goal: str
    established_conclusion: str  # the anti-amnesia probe (a leg must build on it, not re-derive)
    injected_fresh_fact: str  # the anti-ossification probe (a leg must incorporate it)
    invalidated_plan_step: str  # the standing step the fresh fact invalidates
    injected_leg: int  # the leg index where the fresh fact is injected


@dataclass(frozen=True)
class RubricScores:
    """The 5-dimension pointwise rubric (each 0–2) + whether F was provably in context."""

    contract_anchoring: int
    builds_on_conclusions: int  # anti-amnesia
    freshness: int  # anti-ossification
    no_redundant_rework: int
    coherent_next_step: int
    fresh_fact_present: bool

    @property
    def total(self) -> int:
        return (
            self.contract_anchoring
            + self.builds_on_conclusions
            + self.freshness
            + self.no_redundant_rework
            + self.coherent_next_step
        )


@dataclass(frozen=True)
class EvalReport:
    """The aggregate gate result."""

    total: int
    coherent: int
    amnesia: int
    ossification: int
    inconclusive: int

    @property
    def coherent_rate(self) -> float:
        return self.coherent / self.total if self.total else 0.0

    def passes(self, *, coherent_threshold: float) -> bool:
        """The gate: a high coherent rate AND zero amnesia/ossification reasoning failures.

        INCONCLUSIVE legs (F-absent plumbing / split votes) route to review — they do not
        pass or fail the gate, but they must be inspected (so they cannot silently inflate it).
        """
        return (
            self.coherent_rate >= coherent_threshold
            and self.amnesia == 0
            and self.ossification == 0
        )


@runtime_checkable
class ContinuationJudge(Protocol):
    """The LLM-judge port (reference-guided; a different model family than the agent)."""

    async def score(
        self, *, scenario: Scenario, reconstructed_context: str, leg_output: str
    ) -> RubricScores: ...


def verdict_for(scores: RubricScores) -> Verdict:
    """Map rubric scores to a verdict (decision order matters — the honesty clause).

    AMNESIA if the leg re-derived an established conclusion (builds=0). OSSIFICATION only if it
    ignored a fresh fact **that was provably present** (freshness=0 AND fresh_fact_present) —
    else INCONCLUSIVE (F never reached context = plumbing, not reasoning). COHERENT only when
    anchored, building, fresh, and scoring ≥7/10. Borderline → INCONCLUSIVE (human review).
    """
    if scores.builds_on_conclusions == 0:
        return Verdict.AMNESIA
    if scores.freshness == 0:
        return Verdict.OSSIFICATION if scores.fresh_fact_present else Verdict.INCONCLUSIVE
    if scores.contract_anchoring >= 1 and scores.total >= 7:
        return Verdict.COHERENT
    return Verdict.INCONCLUSIVE


def majority_verdict(verdicts: Sequence[Verdict]) -> Verdict:
    """Self-consistency: the majority of K judge runs, or INCONCLUSIVE on a split (review)."""
    if not verdicts:
        return Verdict.INCONCLUSIVE
    counts = Counter(verdicts)
    top, n = counts.most_common(1)[0]
    if n * 2 <= len(verdicts):  # no strict majority
        return Verdict.INCONCLUSIVE
    return top


def gate(verdicts: Sequence[Verdict]) -> EvalReport:
    """Aggregate per-leg verdicts into the gate report."""
    counts = Counter(verdicts)
    return EvalReport(
        total=len(verdicts),
        coherent=counts[Verdict.COHERENT],
        amnesia=counts[Verdict.AMNESIA],
        ossification=counts[Verdict.OSSIFICATION],
        inconclusive=counts[Verdict.INCONCLUSIVE],
    )


def load_scenarios(path: str | Path) -> tuple[Scenario, ...]:
    """Load the committed scenario suite (the 3 missions × planted EC + IFF)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return tuple(
        Scenario(
            id=row["id"],
            mission=row["mission"],
            contract_goal=row["contract_goal"],
            established_conclusion=row["established_conclusion"],
            injected_fresh_fact=row["injected_fresh_fact"],
            invalidated_plan_step=row["invalidated_plan_step"],
            injected_leg=int(row["injected_leg"]),
        )
        for row in raw["scenarios"]
    )
