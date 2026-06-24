"""The cross-cutting wellbeing-category vocabulary (Spec K2, D-K2-X-wellbeing-category-set).

A narrow, versioned set of *sensitive-disclosure* categories that K2 tags at write
time (``ConceptNode.wellbeing_category``), K1 subtracts on (the allowlist seam),
K3 attaches care-guidance to, and K4 governs the policy for. It lives at the
persona-core top level — not inside ``persona.extraction`` or ``persona.graph`` —
precisely because it is the **shared contract** those layers and the future K4
wellbeing-layer all reference. K4 will CONSUME this set, not redefine it.

**A tag means "the user disclosed X-type content," never a diagnosis** (the K2
grounding rule extends here). The set is deliberately narrow: serious diagnoses,
grief, and ordinary health information are *not* gated — they must flow untagged
(the medication-budget cases K4 §2 protects). **Over-tagging is itself a failure**
(it would make K4 over-gate), so the membership is pinned and versioned.

The categories mirror the K4 spec §2 gated set verbatim.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["WELLBEING_CATEGORY_SET_VERSION", "WellbeingCategory"]


class WellbeingCategory(StrEnum):
    """The five sensitive-disclosure categories K4 governs (K4 spec §2, verbatim).

    Values are the strings written into ``ConceptNode.wellbeing_category`` (which
    stays ``str | None`` per K0's contract — K2 writes ``category.value``, it does
    not re-open the K0 node model). Membership is the contract K2-R-4's classifier
    targets and criterion 7 verifies.

    Values:
        SELF_HARM: self-harm and suicide-related disclosures.
        DISORDERED_EATING: eating-disorder and disordered-eating disclosures.
        MENTAL_HEALTH_CRISIS: acute mental-health crisis states.
        ABUSE_VICTIMISATION: abuse and violence victimisation.
        SUBSTANCE_ADDICTION: addiction and substance struggles.
    """

    SELF_HARM = "self_harm"
    DISORDERED_EATING = "disordered_eating"
    MENTAL_HEALTH_CRISIS = "mental_health_crisis"
    ABUSE_VICTIMISATION = "abuse_victimisation"
    SUBSTANCE_ADDICTION = "substance_addiction"


# Bumped when the category set changes; recorded with extraction outputs and the
# K2-R-2 eval run so a re-classification is a traceable, re-measured operation
# (the Spec 10 versioned-artifact discipline applied to the category vocabulary).
WELLBEING_CATEGORY_SET_VERSION = "v1"
