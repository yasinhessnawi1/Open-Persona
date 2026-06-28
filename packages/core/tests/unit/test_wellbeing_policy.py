"""The K4 wellbeing policy artifact (Spec K4, T1; K4-D-1/2/4/6).

Pins the *structure* the three seams consume: the per-category mode (share-with-care
vs context-gated), the never-store boundary, the recency-weighted gate strength, and
the core subtraction gate. The care *text* is T2; these tests assert mode/gate/boundary
only. The load-bearing properties: the positive-case categories (disordered-eating,
substance) are NEVER gated, and the historical band gates *more* strongly than acute.
"""

from __future__ import annotations

import pytest
from persona.wellbeing import WellbeingCategory
from persona.wellbeing_policy import (
    K4_POLICY_VERSION,
    MEANS_REDACTION_CATEGORIES,
    RecencyBand,
    WellbeingMode,
    carries_means_redaction,
    is_gate_eligible,
    mode_for,
    parse_category,
    relevance_lift_threshold,
    should_subtract,
)

# The concretised Mode map (K4-D-2): the detailed-crisis/trauma subset is gate-eligible;
# the two positive-case categories are share-with-care only.
_GATE_ELIGIBLE = {
    WellbeingCategory.SELF_HARM,
    WellbeingCategory.MENTAL_HEALTH_CRISIS,
    WellbeingCategory.ABUSE_VICTIMISATION,
}
_SHARE_WITH_CARE_ONLY = {
    WellbeingCategory.DISORDERED_EATING,
    WellbeingCategory.SUBSTANCE_ADDICTION,
}


def test_policy_version_is_pinned() -> None:
    assert K4_POLICY_VERSION == "v1"


class TestModeMapping:
    @pytest.mark.parametrize("category", sorted(_GATE_ELIGIBLE))
    def test_crisis_trauma_subset_is_context_gated(self, category: WellbeingCategory) -> None:
        assert mode_for(category) is WellbeingMode.CONTEXT_GATED
        assert is_gate_eligible(category)

    @pytest.mark.parametrize("category", sorted(_SHARE_WITH_CARE_ONLY))
    def test_positive_case_categories_are_share_with_care_only(
        self, category: WellbeingCategory
    ) -> None:
        # The fitness/ED case is safe BECAUSE the persona knows-with-care; the budgeting
        # persona must keep including disclosed treatment costs. Gating either would break
        # a hard acceptance criterion — over-gating fails the spec as surely as under-.
        assert mode_for(category) is WellbeingMode.SHARE_WITH_CARE
        assert not is_gate_eligible(category)

    def test_every_category_has_a_mode(self) -> None:
        # Exhaustive: no category falls through to an undefined mode.
        assert {mode_for(c) for c in WellbeingCategory} <= set(WellbeingMode)
        assert all(isinstance(mode_for(c), WellbeingMode) for c in WellbeingCategory)

    def test_the_gate_eligible_set_is_exactly_three(self) -> None:
        # Narrow by design — pin it so an expansion is a deliberate, reviewed change.
        assert {c for c in WellbeingCategory if is_gate_eligible(c)} == _GATE_ELIGIBLE


class TestParseCategory:
    def test_valid_value_parses(self) -> None:
        assert parse_category("self_harm") is WellbeingCategory.SELF_HARM

    def test_none_is_none(self) -> None:
        assert parse_category(None) is None

    def test_unknown_or_legacy_value_is_none_not_an_error(self) -> None:
        # Fail-open to the dominant mode: an unrecognised tag (e.g. the old
        # "mental_health" placeholder) never crashes and never accidentally gates.
        assert parse_category("mental_health") is None
        assert parse_category("") is None


class TestNeverStoreBoundary:
    def test_self_harm_carries_means_redaction(self) -> None:
        assert carries_means_redaction(WellbeingCategory.SELF_HARM)
        assert WellbeingCategory.SELF_HARM in MEANS_REDACTION_CATEGORIES

    @pytest.mark.parametrize(
        "category",
        [c for c in WellbeingCategory if c is not WellbeingCategory.SELF_HARM],
    )
    def test_other_categories_do_not_by_default(self, category: WellbeingCategory) -> None:
        # The boundary is tiny (K4-D-6) — extensible only on concrete evidence.
        assert not carries_means_redaction(category)


class TestRecencyStrength:
    def test_historical_gates_more_strongly_than_acute(self) -> None:
        # The criterion-6 property: old crisis/trauma needs a STRONGER topical signal
        # to lift — so its lift floor is higher.
        assert relevance_lift_threshold(RecencyBand.HISTORICAL) > relevance_lift_threshold(
            RecencyBand.ACUTE
        )

    @pytest.mark.parametrize("band", list(RecencyBand))
    def test_thresholds_are_in_unit_range(self, band: RecencyBand) -> None:
        assert 0.0 <= relevance_lift_threshold(band) <= 1.0


class TestShouldSubtract:
    def test_share_with_care_is_never_subtracted(self) -> None:
        # Even with no relevance and an unopened topic, a share-with-care node flows.
        for category in _SHARE_WITH_CARE_ONLY:
            assert not should_subtract(
                category, recency=RecencyBand.ACUTE, topic_opened=False, relevance=0.0
            )

    def test_topic_opened_always_lifts_the_gate(self) -> None:
        # The user raising it always surfaces the knowledge (with care) — the
        # anti-uncanny rule. True even for irrelevant + historical.
        assert not should_subtract(
            WellbeingCategory.SELF_HARM,
            recency=RecencyBand.HISTORICAL,
            topic_opened=True,
            relevance=0.0,
        )

    def test_gate_eligible_unopened_unrelated_is_subtracted(self) -> None:
        assert should_subtract(
            WellbeingCategory.MENTAL_HEALTH_CRISIS,
            recency=RecencyBand.ACUTE,
            topic_opened=False,
            relevance=0.0,
        )

    def test_relevant_conversation_lifts_the_gate(self) -> None:
        # Above the lift floor ⇒ "the conversation genuinely concerns it" ⇒ surfaced.
        assert not should_subtract(
            WellbeingCategory.ABUSE_VICTIMISATION,
            recency=RecencyBand.ACUTE,
            topic_opened=False,
            relevance=0.99,
        )

    def test_recency_changes_the_lift_outcome_for_the_same_relevance(self) -> None:
        # A relevance BETWEEN the two floors lifts the acute gate but NOT the historical
        # one — the recency strength is observable end to end, not just in the threshold.
        acute_floor = relevance_lift_threshold(RecencyBand.ACUTE)
        hist_floor = relevance_lift_threshold(RecencyBand.HISTORICAL)
        mid = (acute_floor + hist_floor) / 2.0
        assert mid >= acute_floor  # lifts acute
        assert not should_subtract(
            WellbeingCategory.SELF_HARM,
            recency=RecencyBand.ACUTE,
            topic_opened=False,
            relevance=mid,
        )
        assert should_subtract(  # still gated when historical
            WellbeingCategory.SELF_HARM,
            recency=RecencyBand.HISTORICAL,
            topic_opened=False,
            relevance=mid,
        )
