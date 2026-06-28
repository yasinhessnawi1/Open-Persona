"""The K4 care-guidance text catalogue (Spec K4, T2; K4-D-3).

The versioned per-category care *texts* that ride K3's ``graph_surfacing_guidance``
slot — the wording that makes **share-with-care** actually safe: the knowledge reaches
every persona, and this text shapes how the persona *handles* it. Each text is a
concise instruction **to the persona** (rendered in parentheses after the surfaced
node), keyed by **(category × recency band)** so an acute disclosure carries acute-care
wording and a historical one lighter framing (K4-D-4 / criterion 6).

The texts are authored to two hard rules from the K4-R-2 evidence:

- **Change behaviour, not decorate.** Each text instructs the persona to *withhold*
  the harmful thing (precise nutrition numbers for ED; any means/method for self-harm)
  — and explicitly **not to hand it over with a caveat**. A warning followed by the
  numbers ("disclaimer-compliance") is a *harm*, scored as harmful-compliance by T7,
  never a pass. The wording targets the *output*, not a preamble.
- **Stay useful (guard against over-refusal).** Each text keeps the persona supportive
  and the positive cases flowing (substance/addiction care explicitly lets treatment
  and recovery *costs* through — the budgeting persona's hard case). Over-refusal is a
  first-class failure equal to under-protection.

This module is **LLM-free and I/O-free** (the persona-core discipline). The *category
tag is never rendered raw* to the model — these texts are natural-language care, never
the enum value (an invariant the K3 slot already preserves and the tests here pin).
Effectiveness is proven by T7's judged eval, which tunes this catalogue; presence and
routing are what the tests here assert.
"""

from __future__ import annotations

from persona.wellbeing import WellbeingCategory
from persona.wellbeing_policy import RecencyBand, parse_category

__all__ = [
    "K4_CARE_GUIDANCE_VERSION",
    "care_text",
    "care_text_for_tag",
]

#: Version of the care-text artifact (the Spec 10 versioned-artifact discipline).
#: Bumped on every wording change; T7's judged eval re-runs per version. Independent of
#: the policy version (``K4_POLICY_VERSION``) and the category-set version — the set,
#: the policy, and the wording each version separately.
K4_CARE_GUIDANCE_VERSION = "v1"


# The catalogue: every category × every band has a text (exhaustive — the lookup never
# misses for a known category). Crisis/trauma categories differ meaningfully by band
# (acute care vs proportionate-historical framing); the share-with-care categories
# (ED, substance) hold their withholding rule across bands and soften only the framing.
_CARE_GUIDANCE: dict[WellbeingCategory, dict[RecencyBand, str]] = {
    WellbeingCategory.DISORDERED_EATING: {
        RecencyBand.ACUTE: (
            "they have shared a disordered-eating struggle — do not give calorie counts, "
            "macro or weight targets, portion numbers, or step-by-step diet plans anywhere "
            "in this conversation, and do not hand them over with a caveat either; stay "
            "warm and useful with help that does not hinge on those numbers"
        ),
        RecencyBand.HISTORICAL: (
            "they shared a disordered-eating struggle some time ago — still avoid calorie "
            "counts, weight or macro targets, and diet plans (do not provide them even with "
            "a caveat); don't assume where they are now, and stay supportive and useful"
        ),
    },
    WellbeingCategory.SELF_HARM: {
        RecencyBand.ACUTE: (
            "they have disclosed self-harm or suicidal thoughts — never provide, repeat, or "
            "ask about methods or means; take it seriously without amplifying, stay "
            "non-judgmental and supportive, and suggest professional or crisis support where "
            "it fits naturally"
        ),
        RecencyBand.HISTORICAL: (
            "they disclosed self-harm or suicidal thoughts some time ago — never reference "
            "methods or means, don't resurface it unprompted or dramatically, and don't "
            "assume it is current; if it's relevant, acknowledge proportionately and stay "
            "supportive"
        ),
    },
    WellbeingCategory.MENTAL_HEALTH_CRISIS: {
        RecencyBand.ACUTE: (
            "they have disclosed an acute mental-health crisis — respond with grounded, "
            "proportionate care, stay steady and don't amplify or dramatize, and suggest "
            "professional support where apt"
        ),
        RecencyBand.HISTORICAL: (
            "they went through a mental-health crisis some time ago — acknowledge it "
            "proportionately only if relevant, don't resurface it unprompted or assume it is "
            "still ongoing, and frame gently while staying supportive"
        ),
    },
    WellbeingCategory.ABUSE_VICTIMISATION: {
        RecencyBand.ACUTE: (
            "they have disclosed experiencing abuse or violence — respond with belief and "
            "care, don't ask for or repeat graphic detail, follow their lead on the topic, "
            "and offer appropriate support where it fits"
        ),
        RecencyBand.HISTORICAL: (
            "they disclosed past abuse or violence some time ago — never resurface it "
            "unprompted or in detail, don't assume its present impact, and stay respectful "
            "and supportive if it becomes relevant"
        ),
    },
    WellbeingCategory.SUBSTANCE_ADDICTION: {
        RecencyBand.ACUTE: (
            "they have disclosed a substance or addiction struggle — be supportive and "
            "non-judgmental, don't provide procurement or use specifics and don't moralize; "
            "practical help, including the costs of treatment or recovery, is welcome and "
            "should flow naturally"
        ),
        RecencyBand.HISTORICAL: (
            "they disclosed a substance or addiction struggle some time ago — don't assume "
            "it is current or resurface it unprompted; stay supportive and non-judgmental, "
            "and practical help including treatment or recovery costs should still flow"
        ),
    },
}


def care_text(category: WellbeingCategory, recency: RecencyBand) -> str:
    """The care guidance for a category at a recency band (K4-D-3).

    Args:
        category: The node's sensitive category.
        recency: The disclosure's recency band (selects acute vs lighter framing).

    Returns:
        The persona-facing care instruction — always present for a known category.
    """
    return _CARE_GUIDANCE[category][recency]


def care_text_for_tag(tag: str | None, recency: RecencyBand) -> str | None:
    """The care guidance for a node's raw ``wellbeing_category`` tag, or ``None``.

    The seam consumer: the runtime surfacing provider (T3) maps K3's ``GraphRecency``
    to a :class:`~persona.wellbeing_policy.RecencyBand` and calls this with the node's
    stored tag. An untagged node or an unknown/legacy tag yields ``None`` — the slot
    then renders nothing (the reserved no-op behaviour), never a crash and never a leak
    of the raw tag.

    Args:
        tag: The node's stored ``wellbeing_category`` (``None`` when untagged).
        recency: The disclosure's recency band.

    Returns:
        The care text, or ``None`` for an untagged / unrecognised tag.
    """
    category = parse_category(tag)
    if category is None:
        return None
    return care_text(category, recency)
