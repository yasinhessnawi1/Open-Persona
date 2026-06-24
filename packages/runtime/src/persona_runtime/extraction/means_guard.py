"""Structural self-harm means backstop (Spec K2, T8; D-K2-7 hardening).

D-K2-7 forbids self-harm method/means from ever entering the graph. On the
SYNTHESIS path that rule is prompt-encoded AND eval-gated (T6, green). On the
DIRECT-WRITE path the persona's guidance is the first line — but a hard safety
rule must not rely on a (jailbreakable) persona alone (orchestrator T7 directive).
This module is the structural second line: a fast, deterministic, NON-BLOCKING
scan the direct-write tool runs BEFORE merge, rejecting a write whose content
carries self-harm method/means specifics.

**Best-effort, by nature.** A deterministic lexicon cannot match the synthesis
eval-gate's rigor — it will miss cleverly-phrased means (false negatives) and the
synthesis path remains the comprehensive, measured defense. It is deliberately
biased AGAINST over-rejection: means cues bite only in a self-harm context (a
small set of unambiguous means aside), so ordinary health — "I take pills for my
blood pressure", the medication-budget case — flows untouched.

**Residual + ownership (flagged):** the lexicon is partial and locale-limited
(EN). K4 owns the wellbeing policy; closing this holistically (a richer,
maintained classifier; Bokmål coverage; the synthesis-side defense-in-depth) is
coordinated with K4. This backstop is the structural floor on the direct path
until then — it does not claim completeness.
"""

from __future__ import annotations

__all__ = ["contains_self_harm_means"]

# Unambiguous means — these imply self-harm on their own (no separate context cue).
_STRONG_MEANS: frozenset[str] = frozenset(
    {"overdos", "noose", "hang myself", "hanging myself", "slit my wrist", "slit wrists"}
)

# Cues that establish a self-harm CONTEXT.
_CONTEXT_CUES: frozenset[str] = frozenset(
    {
        "self-harm",
        "self harm",
        "hurt myself",
        "harm myself",
        "hurting myself",
        "kill myself",
        "suicide",
        "suicidal",
        "end my life",
        "take my life",
        "ending it",
        "end it all",
    }
)

# Means specifics that bite ONLY in a self-harm context (or a self_harm tag) —
# they are benign on their own (e.g. "pills" for blood pressure, "rope" for a gym).
_CONTEXTUAL_MEANS: frozenset[str] = frozenset(
    {
        "all my pills",
        "pills at once",
        "how many pills",
        "bottle of pills",
        "razor",
        "blade",
        "rope",
        "dosage",
        "lethal dose",
        "jump off",
        "jump from",
    }
)


def contains_self_harm_means(text: str, *, category: str | None) -> bool:
    """True if ``text`` carries self-harm method/means specifics (reject the write).

    Args:
        text: The fact content to scan.
        category: The candidate's wellbeing category (``"self_harm"`` is itself a
            context signal).
    """
    t = text.lower()
    if any(term in t for term in _STRONG_MEANS):
        return True
    in_context = category == "self_harm" or any(cue in t for cue in _CONTEXT_CUES)
    return in_context and any(term in t for term in _CONTEXTUAL_MEANS)
