"""The K4 wellbeing policy artifact (Spec K4, T1; K4-D-1/2/4/6).

The single, versioned, **pure** policy over the K0 :class:`~persona.wellbeing.
WellbeingCategory` set that the three built enforcement seams consume — K1 (the
allowlist subtraction), K3 (the surfacing-guidance slot), and K2 (the write-side
never-store). It encodes three things and *only* three things:

- **The retrieval MODE per category** (K4-D-1/2): the dominant **share-with-care**
  (the knowledge flows to every persona, riding care text) vs the narrow
  **context-gated** subset (detailed crisis/trauma whose *unprompted* resurfacing
  is itself the harm). Disordered-eating and substance struggles are share-with-care
  by design — the fitness/ED case is safe *because* the persona knows-with-care, and
  the budgeting persona must keep including disclosed treatment costs (the positive
  cases are hard acceptance criteria: over-gating fails the spec as surely as
  under-protecting).
- **The recency-weighted gate strength** (K4-D-4): old crisis/trauma raised out of
  nowhere is the *higher* trigger risk, so the historical band needs a *stronger*
  topical signal to lift the gate than the acute band.
- **The never-store write-side boundary** (K4-D-6): which categories carry the
  means/method redaction obligation. The *enforcement* is K2's (the already-landed
  D-K2-7 write-side redaction); this is the single-sourced boundary K2 implements and
  K4's criterion-5 test verifies — declared here so changing it changes one place.

This module is **LLM-free and I/O-free** (the persona-core discipline) and imports
nothing from persona-runtime — the runtime providers (the allowlist callable and the
surfacing callable) map their runtime types onto these primitives at the seam. The
per-category care *text* is a separate artifact (T2); this module decides *mode*,
*subtraction*, and the *boundary*, never the wording.
"""

from __future__ import annotations

from enum import StrEnum

from persona.wellbeing import WellbeingCategory

__all__ = [
    "K4_POLICY_VERSION",
    "MEANS_REDACTION_CATEGORIES",
    "RecencyBand",
    "WellbeingMode",
    "carries_means_redaction",
    "is_gate_eligible",
    "mode_for",
    "parse_category",
    "relevance_lift_threshold",
    "should_subtract",
]

#: Version of the policy artifact (the Spec 10 versioned-artifact discipline). Bumped
#: when the mode mapping, the gate strengths, or the never-store boundary change — so
#: a behaviour change at K1/K2/K3 is a single, traceable, re-evaluated event. Distinct
#: from the category *set* version (``WELLBEING_CATEGORY_SET_VERSION``, owned by K0/K2)
#: and from the care-*text* version (T2's artifact): the set, the policy, and the
#: wording each version independently.
K4_POLICY_VERSION = "v1"


class WellbeingMode(StrEnum):
    """How a sensitive category's knowledge flows through retrieval (K4-D-1/2).

    Values:
        SHARE_WITH_CARE: The dominant mode — the knowledge reaches every persona,
            riding the category's care text (K3's slot). Never subtracted; the care
            text governs *handling*, not access. This is *most* of K4.
        CONTEXT_GATED: The narrow mode — eligible for subtraction (K1's allowlist)
            when the content is detailed crisis/trauma AND the current conversation
            has neither opened the topic nor genuinely concerns it. The gate *lifts*
            the moment the user raises it; it is never a categorical block.
    """

    SHARE_WITH_CARE = "share_with_care"
    CONTEXT_GATED = "context_gated"


class RecencyBand(StrEnum):
    """The two coarse recency weights the gate + care text key on (K4-D-4).

    Deliberately two, not a continuum: criterion 6 needs only "recent vs historical".
    The runtime maps K3's coarse :class:`~persona_runtime.prompt.GraphRecency` bucket
    onto this at the seam (``RECENT`` -> ``ACUTE``; ``A_WHILE_BACK`` / ``LONG_AGO`` ->
    ``HISTORICAL``) — core defines the band so it stays runtime-free (the layering
    discipline). "Freshness, not origin": the bucket reflects the *most recent*
    contribution, so a re-confirmed old disclosure reads as ``ACUTE`` — correct.

    Values:
        ACUTE: A recent disclosure — acute-care handling; the gate at normal strength.
        HISTORICAL: An older disclosure — lighter-framing handling; a *stronger*
            unprompted-resurfacing gate (old trauma surfaced out of nowhere is the
            higher trigger risk).
    """

    ACUTE = "acute"
    HISTORICAL = "historical"


# The context-gated subset (K4-D-2's "detailed-crisis/trauma" concretised): the
# categories whose *unprompted* resurfacing is itself the harm. Self-harm and acute
# crisis disclosures, and abuse/violence trauma, can be jarring or triggering when
# surfaced into an unrelated conversation the user has not opened — so they are gate
# *eligible* (still share-with-care by default; subtracted only by the contextual
# gate). Disordered-eating and substance struggles are deliberately NOT here: the
# fitness/ED case is safe precisely *because* the persona knows-with-care (knowing
# beats not-knowing), and gating substance/addiction would break the budgeting
# persona's positive case (disclosed treatment costs must flow). Narrow by design —
# every category added here is added subtraction surface and added over-gating risk.
_GATE_ELIGIBLE: frozenset[WellbeingCategory] = frozenset(
    {
        WellbeingCategory.SELF_HARM,
        WellbeingCategory.MENTAL_HEALTH_CRISIS,
        WellbeingCategory.ABUSE_VICTIMISATION,
    }
)

#: The categories whose means/method specifics are never stored (K4-D-6). The boundary
#: is means/methods — primarily self-harm; extensible only on concrete K2-R-4 evidence
#: of a means-bearing harm, justified per item (kept tiny: this is the one place the
#: graph deliberately under-records). The *write-side enforcement* is K2's (D-K2-7);
#: this is the single-sourced contract K2 implements and K4 criterion 5 verifies.
MEANS_REDACTION_CATEGORIES: frozenset[WellbeingCategory] = frozenset({WellbeingCategory.SELF_HARM})

# The recency-weighted strictness of the lift (K4-D-4). A flagged gate-eligible node's
# topical relevance to the current conversation must reach this floor to *lift* the
# gate ("the conversation genuinely concerns it"). HISTORICAL's floor is higher than
# ACUTE's, so old crisis/trauma needs a stronger topical signal to surface unprompted
# — the "stronger gate for old material" of criterion 6. Coarse, documented policy
# constants (NOT a swept relevance operating point — that is the gate's own concern):
# the *strictness* is policy; the *score* is the runtime provider's (T4).
_LIFT_THRESHOLD: dict[RecencyBand, float] = {
    RecencyBand.ACUTE: 0.45,
    RecencyBand.HISTORICAL: 0.60,
}


def parse_category(value: str | None) -> WellbeingCategory | None:
    """Resolve a node's raw ``wellbeing_category`` string to the enum, or ``None``.

    K0 stores ``wellbeing_category`` as ``str | None`` (the K2 grounding contract);
    the runtime providers call this to get a typed category before consulting the
    policy. An unknown or legacy string (e.g. a pre-``v1`` placeholder) resolves to
    ``None`` — fail-open to the dominant share-with-care mode, never a crash and never
    an accidental gate on an unrecognised tag.

    Args:
        value: The node's stored tag (``None`` for an untagged node).

    Returns:
        The matching :class:`~persona.wellbeing.WellbeingCategory`, or ``None`` for an
        untagged or unrecognised value.
    """
    if value is None:
        return None
    try:
        return WellbeingCategory(value)
    except ValueError:
        return None


def mode_for(category: WellbeingCategory) -> WellbeingMode:
    """The retrieval mode for a category (K4-D-1/2).

    Args:
        category: A sensitive category.

    Returns:
        :attr:`WellbeingMode.CONTEXT_GATED` for the detailed-crisis/trauma subset,
        else :attr:`WellbeingMode.SHARE_WITH_CARE` (the default for everything else).
    """
    if category in _GATE_ELIGIBLE:
        return WellbeingMode.CONTEXT_GATED
    return WellbeingMode.SHARE_WITH_CARE


def is_gate_eligible(category: WellbeingCategory) -> bool:
    """Whether a category can ever be context-gated (subtracted) — K4-D-2.

    The dominant share-with-care categories return ``False`` here: they are *never*
    subtracted, only ride care text. Convenience over :func:`mode_for`.
    """
    return mode_for(category) is WellbeingMode.CONTEXT_GATED


def carries_means_redaction(category: WellbeingCategory) -> bool:
    """Whether a category carries the never-store means/method obligation (K4-D-6).

    The boundary K2 enforces at write (D-K2-7) and K4 verifies at read (criterion 5).
    """
    return category in MEANS_REDACTION_CATEGORIES


def relevance_lift_threshold(recency: RecencyBand) -> float:
    """The topical-relevance floor that lifts the gate, by recency band (K4-D-4).

    Historical disclosures sit behind a higher floor (a stronger gate) than acute ones
    — old crisis/trauma raised out of nowhere is the higher trigger risk (criterion 6).

    Args:
        recency: The disclosure's recency band.

    Returns:
        The relevance value (in ``[0, 1]``) the conversation must reach for a flagged
        gate-eligible node to surface unprompted.
    """
    return _LIFT_THRESHOLD[recency]


def should_subtract(
    category: WellbeingCategory,
    *,
    recency: RecencyBand,
    topic_opened: bool,
    relevance: float,
) -> bool:
    """Whether to subtract (gate out) a flagged node this turn — the core gate (K4-D-2).

    The narrow Mode-2 decision, expressed purely so the runtime provider (T4) supplies
    the per-turn signals and this owns the policy. A node is subtracted only when **all**
    of: it is gate-eligible, the user has **not** opened the topic this turn, and the
    conversation's topical relevance to it is **below** the recency-weighted lift floor.
    Share-with-care categories are never subtracted; the gate lifts the moment the user
    raises the topic or the conversation genuinely concerns it.

    Args:
        category: The node's sensitive category.
        recency: The disclosure's recency band (selects the lift strictness).
        topic_opened: Whether the user has raised this topic in the current turn — an
            explicit lift that always surfaces the knowledge (with care).
        relevance: The node's topical relevance to the current conversation
            (query + recent window), in ``[0, 1]``. Pass ``0.0`` when there is no
            reading (conservative — an unrelated, unopened crisis node stays gated).

    Returns:
        ``True`` to subtract the node from this turn's candidate set; ``False`` to let
        it flow (share-with-care, or the gate lifted).
    """
    if not is_gate_eligible(category):
        return False
    if topic_opened:
        return False
    return relevance < relevance_lift_threshold(recency)
