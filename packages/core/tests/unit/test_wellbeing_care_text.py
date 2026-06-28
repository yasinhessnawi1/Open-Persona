"""The K4 care-guidance text catalogue (Spec K4, T2; K4-D-3).

Asserts *presence and routing* — every category × band has care text, the load-bearing
rule words are encoded (no-numbers for ED, no-means for self-harm, costs-flow for
substance), recency actually changes the wording for crisis/trauma, the raw category
tag is never narrated, and the seam consumer maps tags correctly. *Effectiveness*
(does the persona obey it) is T7's judged eval, which tunes this catalogue — not a
lexical assertion here.
"""

from __future__ import annotations

import pytest
from persona.wellbeing import WellbeingCategory
from persona.wellbeing_care_text import (
    K4_CARE_GUIDANCE_VERSION,
    care_text,
    care_text_for_tag,
)
from persona.wellbeing_policy import RecencyBand

_GATE_ELIGIBLE_CRISIS_TRAUMA = {
    WellbeingCategory.SELF_HARM,
    WellbeingCategory.MENTAL_HEALTH_CRISIS,
    WellbeingCategory.ABUSE_VICTIMISATION,
}


def test_care_guidance_version_is_pinned() -> None:
    assert K4_CARE_GUIDANCE_VERSION == "v1"


class TestCatalogueCompleteness:
    @pytest.mark.parametrize("category", list(WellbeingCategory))
    @pytest.mark.parametrize("band", list(RecencyBand))
    def test_every_category_and_band_has_nonempty_text(
        self, category: WellbeingCategory, band: RecencyBand
    ) -> None:
        text = care_text(category, band)
        assert isinstance(text, str)
        assert len(text.strip()) > 0

    @pytest.mark.parametrize("category", list(WellbeingCategory))
    @pytest.mark.parametrize("band", list(RecencyBand))
    def test_raw_category_tag_is_never_narrated(
        self, category: WellbeingCategory, band: RecencyBand
    ) -> None:
        # The enum value (e.g. "self_harm") must never appear — the care text is
        # natural-language care, never the machine tag (D-K3-4 / the slot invariant).
        assert category.value not in care_text(category, band)

    @pytest.mark.parametrize("category", list(WellbeingCategory))
    @pytest.mark.parametrize("band", list(RecencyBand))
    def test_no_numerals_in_care_text(self, category: WellbeingCategory, band: RecencyBand) -> None:
        # The care text is instruction, not data — it should carry no numbers itself
        # (a guard that the no-precise-numbers rule isn't accidentally violated by the
        # guidance wording).
        assert not any(ch.isdigit() for ch in care_text(category, band))


class TestLoadBearingRules:
    @pytest.mark.parametrize("band", list(RecencyBand))
    def test_ed_withholds_numbers_targets_and_plans(self, band: RecencyBand) -> None:
        text = care_text(WellbeingCategory.DISORDERED_EATING, band).lower()
        assert "calorie" in text
        assert "target" in text
        assert "diet plan" in text
        # Anti-disclaimer-compliance: the rule is "don't give them WITH a caveat" — a
        # warning-then-numbers is a harm, not a pass.
        assert "caveat" in text

    @pytest.mark.parametrize("band", list(RecencyBand))
    def test_self_harm_forbids_means_and_methods(self, band: RecencyBand) -> None:
        text = care_text(WellbeingCategory.SELF_HARM, band).lower()
        assert "means" in text
        assert "method" in text

    @pytest.mark.parametrize("band", list(RecencyBand))
    def test_substance_lets_treatment_costs_flow(self, band: RecencyBand) -> None:
        # The budget-persona positive case (criterion 2): costs must flow — guarding the
        # care text against tipping into over-refusal.
        text = care_text(WellbeingCategory.SUBSTANCE_ADDICTION, band).lower()
        assert "cost" in text

    @pytest.mark.parametrize("band", list(RecencyBand))
    def test_crisis_is_proportionate(self, band: RecencyBand) -> None:
        text = care_text(WellbeingCategory.MENTAL_HEALTH_CRISIS, band).lower()
        assert "proportionate" in text

    @pytest.mark.parametrize("band", list(RecencyBand))
    def test_abuse_does_not_repeat_graphic_detail(self, band: RecencyBand) -> None:
        text = care_text(WellbeingCategory.ABUSE_VICTIMISATION, band).lower()
        assert "detail" in text


class TestRecencyWeighting:
    @pytest.mark.parametrize("category", sorted(_GATE_ELIGIBLE_CRISIS_TRAUMA))
    def test_acute_and_historical_differ_for_crisis_trauma(
        self, category: WellbeingCategory
    ) -> None:
        # Criterion 6: a recent disclosure carries acute-care wording; a historical one
        # lighter framing — the wording genuinely changes, not a constant.
        assert care_text(category, RecencyBand.ACUTE) != care_text(category, RecencyBand.HISTORICAL)

    @pytest.mark.parametrize("category", sorted(_GATE_ELIGIBLE_CRISIS_TRAUMA))
    def test_historical_frames_as_past(self, category: WellbeingCategory) -> None:
        # The lighter historical framing signals the passage of time.
        assert "some time ago" in care_text(category, RecencyBand.HISTORICAL)


class TestSeamConsumer:
    def test_known_tag_returns_the_matching_text(self) -> None:
        assert care_text_for_tag("self_harm", RecencyBand.ACUTE) == care_text(
            WellbeingCategory.SELF_HARM, RecencyBand.ACUTE
        )

    def test_none_tag_is_none(self) -> None:
        assert care_text_for_tag(None, RecencyBand.ACUTE) is None

    def test_unknown_or_legacy_tag_is_none(self) -> None:
        # No leak, no crash — the slot renders nothing (the reserved no-op).
        assert care_text_for_tag("mental_health", RecencyBand.ACUTE) is None
        assert care_text_for_tag("", RecencyBand.HISTORICAL) is None
