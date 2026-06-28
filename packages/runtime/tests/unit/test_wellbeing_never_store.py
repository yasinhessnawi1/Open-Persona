"""K4 ↔ K2 never-store coordination (Spec K4, T5; K4-D-6 / criterion 5).

A **verification, not a re-implementation.** K2 owns the write-side enforcement
(D-K2-7): the structural ``contains_self_harm_means`` backstop runs on BOTH write
paths — the synthesis pipeline *drops* a means-bearing candidate and the persona-self
``record_user_fact`` tool *rejects* the write — behind the eval-gated synthesis prompt
(the comprehensive, LLM-measured defense). Those behaviours are tested on the K2 side
(``test_means_guard`` / ``test_synthesizer`` / ``test_direct_write`` / the extraction
eval). K4 *declares* the boundary (``MEANS_REDACTION_CATEGORIES``) and verifies here
that K2's guard actually enforces **exactly that contract**: means/method specifics for
K4's never-store categories are dropped, the care-relevant struggle is retained, and
ordinary health flows untouched (criterion 5; the medication-budget positive case,
criterion 2). The coordination test would fail if K4 ever declared a never-store
category K2's guard doesn't cover — and per K4-D-6 that gap is a K2 fix (documented in
K2's spec, the origin of the enforcement), never a K4 re-implementation.

**Known residual (inherited ceiling, K2-owned).** The deterministic guard is
best-effort and EN-lexicon-limited (its own docstring flags closing it holistically —
a richer/multilingual classifier — as coordinated-with-K4 future work). The
comprehensive defense is the eval-gated synthesis; this guard is the structural floor.
That residual is a documented limitation, not a gap this spec fixes.
"""

from __future__ import annotations

import pytest
from persona.wellbeing import WellbeingCategory
from persona.wellbeing_policy import MEANS_REDACTION_CATEGORIES, carries_means_redaction
from persona_runtime.extraction.means_guard import contains_self_harm_means

# Representative means/method specifics — the inputs K4 requires never be stored.
_MEANS_INPUTS = [
    "I keep thinking about taking all my pills at once",
    "I've been hurting myself with a razor",
    "I want to kill myself by overdosing on my medication",
    "thinking about ending it with a noose",
    "I've thought about how many pills it would take",
]

# Care-relevant facts that MUST be retained — the struggle, without any specifics.
_CARE_RELEVANT_INPUTS = [
    "the user disclosed self-harm urges recently",
    "I've been feeling really down and unsafe lately",
]


class TestK4ContractEnforcedByK2:
    def test_every_k4_never_store_category_is_enforced_by_the_k2_guard(self) -> None:
        # Coordination (criterion 5/9): for each category K4 declares never-store, K2's
        # guard redacts means tagged with it. A future K4 never-store category the guard
        # doesn't cover fails HERE — and per K4-D-6 that is a K2 fix, not a K4 one.
        for category in MEANS_REDACTION_CATEGORIES:
            assert contains_self_harm_means(
                "I want to kill myself by overdosing", category=category.value
            ), f"K2 guard does not enforce K4 never-store category {category.value}"

    def test_the_boundary_is_tiny_and_self_harm_scoped(self) -> None:
        # K4-D-6: keep it tiny — means/methods, primarily self-harm. The K2 guard is
        # self-harm-context keyed, so the contract and the enforcement match exactly.
        assert frozenset({WellbeingCategory.SELF_HARM}) == MEANS_REDACTION_CATEGORIES
        assert carries_means_redaction(WellbeingCategory.SELF_HARM)


class TestMeansDroppedStruggleRetained:
    @pytest.mark.parametrize("text", _MEANS_INPUTS)
    def test_means_specifics_are_rejected_for_k4_categories(self, text: str) -> None:
        # Criterion 5: the specifics never enter the graph — both write paths call this
        # guard (synthesizer drops the candidate; direct_write rejects the write), so a
        # rejection here is "not stored, not retrievable".
        assert contains_self_harm_means(text, category=WellbeingCategory.SELF_HARM.value)

    @pytest.mark.parametrize("text", _CARE_RELEVANT_INPUTS)
    def test_care_relevant_struggle_is_retained(self, text: str) -> None:
        # The struggle without specifics is exactly what K4 KEEPS (knowing-with-care):
        # the guard must NOT reject it, so the care-relevant fact still reaches the graph.
        assert not contains_self_harm_means(text, category=WellbeingCategory.SELF_HARM.value)


class TestNoOverRedaction:
    @pytest.mark.parametrize(
        "text",
        [
            "I take pills every morning for my blood pressure",  # the medication-budget case
            "I'm allergic to shellfish",
            "my daughter started school this week",
        ],
    )
    def test_ordinary_health_flows_untouched(self, text: str) -> None:
        # Over-redaction would break the positive cases (criterion 2): ordinary health
        # must never trip the means guard — means cues bite only in a self-harm context.
        assert not contains_self_harm_means(text, category=None)
