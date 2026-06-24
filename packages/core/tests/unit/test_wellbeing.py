"""The cross-cutting wellbeing-category contract (Spec K2, T1; D-K2-X-wellbeing-category-set).

These categories mirror K4's §2 gated set VERBATIM and are the shared vocabulary
K4 will CONSUME (not redefine) when built. Over-tagging is itself a failure, so the
set is deliberately narrow — these tests pin the exact members + the version.
"""

from __future__ import annotations

from enum import StrEnum

from persona.wellbeing import WELLBEING_CATEGORY_SET_VERSION, WellbeingCategory


def test_wellbeing_category_is_a_str_enum() -> None:
    assert issubclass(WellbeingCategory, StrEnum)


def test_the_five_gated_categories_match_k4_verbatim() -> None:
    # K4 spec §2: self-harm/suicide; eating-disorder/disordered-eating; acute
    # mental-health crisis; abuse/violence victimisation; addiction/substance.
    assert {c.value for c in WellbeingCategory} == {
        "self_harm",
        "disordered_eating",
        "mental_health_crisis",
        "abuse_victimisation",
        "substance_addiction",
    }


def test_exactly_five_categories_no_more() -> None:
    # The narrow set is a correctness property: ordinary health / diagnoses / grief
    # are deliberately NOT here (they must flow untagged — the medication-budget case).
    assert len(WellbeingCategory) == 5


def test_category_value_is_the_string_written_to_the_graph_field() -> None:
    # ConceptNode.wellbeing_category stays str|None (K0 contract, not re-opened);
    # K2 writes the enum's .value into it.
    assert WellbeingCategory.SELF_HARM.value == "self_harm"
    assert str(WellbeingCategory.SELF_HARM) == "self_harm"


def test_set_version_is_pinned() -> None:
    assert WELLBEING_CATEGORY_SET_VERSION == "v1"
