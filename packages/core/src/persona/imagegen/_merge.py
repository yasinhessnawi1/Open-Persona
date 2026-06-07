"""Visual-style prompt merge — deterministic mechanics for D-15-4.

The single public entry point :func:`merge_visual_style` is the
content-first suffix-conditioning template locked by Phase 4 decision
D-15-4: ``f"{prompt}, in the style of {style}"`` when (and only when)
the caller's persona carries a non-empty ``identity.visual_style`` AND
the user prompt does not itself already specify a style.

The conflict-resolution rule has three branches:

1. **Empty / ``None`` style → identity.** The caller passed no style;
   the prompt is returned unchanged.
2. **User-specified style detected → identity (user wins).** Detected
   via three deterministic heuristics in
   :func:`_user_specified_style` — substring ``"in the style of"``;
   ``"as a <modifier> <medium-noun>"`` window; tail-position adjective
   from the closed 20-entry :data:`_KNOWN_STYLE_TAIL` set.
3. **Otherwise → suffix-merge.** Trailing whitespace / period is
   stripped before the comma + ``"in the style of <style>"`` suffix is
   appended.

Why suffix, not prefix: diffusion + transformer-image models attend
more strongly to early tokens (dominant-noun-phrase position). The
content-first ordering preserves user intent; the persona style
flavours by riding the lower-attention tail. Prefix-conditioning
(``"<style>: <prompt>"``) would risk style overriding content — the
exact failure mode acceptance criterion #6 forbids.

Why string concatenation, not a provider-specific style param: OpenAI
``gpt-image-1`` has no ``style`` param (DALL-E 3's ``style:
vivid|natural`` was removed); Flux 1.1 [pro] has no separate style
param either. String-level merge is the only uniform option across
the two D-15-1 providers, and aligns with the Spec 02 "one neutral
surface, per-provider translation" discipline.

This module ships **deterministic mechanics only** — the empirical
claim that the merged prompt produces a recognisable subject with a
flavoured aesthetic against a live model is the T19
``@pytest.mark.external`` smoke suite's burden, not this module's.

References:
    docs/specs/phase2/spec_15/decisions.md D-15-4;
    docs/specs/phase2/spec_15/research.md §3.
"""

from __future__ import annotations

__all__ = [
    "merge_visual_style",
]


_KNOWN_STYLE_TAIL: frozenset[str] = frozenset(
    {
        "photorealistic",
        "anime",
        "watercolour",
        "watercolor",
        "oil painting",
        "sketch",
        "3d render",
        "pixel art",
        "cinematic",
        "illustration",
        "minimalist",
        "abstract",
        "impressionist",
        "vintage",
        "cyberpunk",
        "low poly",
        "concept art",
        "noir",
        "sepia",
        "monochrome",
        "cartoon",
    }
)
"""Closed 20-entry adjective set for tail-position style detection.

Enumerated verbatim in
``docs/specs/phase2/spec_15/tasks.md`` §T11 and in
``docs/specs/phase2/spec_15/decisions.md`` D-15-4. Extension of this
set requires code review — adding entries broadens "user-wins"
detection and shrinks the surface where persona ``visual_style`` is
merged in; both directions need deliberate consideration.
"""


# Medium-noun set for the ``"as a <modifier> <medium>"`` window heuristic.
# Closed-set; tracked alongside :data:`_KNOWN_STYLE_TAIL` because both
# express the same "the user already specified a style" surface.
_AS_A_MEDIUM_NOUNS: frozenset[str] = frozenset(
    {
        "painting",
        "sketch",
        "render",
        "illustration",
        "drawing",
        "photo",
        "photograph",
    }
)


def merge_visual_style(prompt: str, style: str | None) -> str:
    """Merge a persona ``visual_style`` into a generation prompt (D-15-4).

    Deterministic string operation — no model call, no provider
    knowledge, no token counting. Three branches:

    1. ``style`` is ``None`` or whitespace-only → ``prompt`` returned
       unchanged.
    2. ``prompt`` already specifies a style per
       :func:`_user_specified_style` → ``prompt`` returned unchanged
       (user wins per D-15-4 sub-rule).
    3. Otherwise → ``f"{prompt_without_trailing_period_or_space}, in
       the style of {style.strip()}"``.

    Args:
        prompt: The user prompt as it would reach the image backend.
            Newlines and arbitrary unicode are preserved.
        style: The persona's ``identity.visual_style``. ``None`` and
            whitespace-only values are both treated as "no style
            configured" and yield the identity branch.

    Returns:
        The prompt as it should be passed to the
        :meth:`persona.imagegen.protocol.ImageBackend.generate` call.
        Branches 1 and 2 return the original string object (identity);
        branch 3 returns a newly constructed string.

    Notes:
        Whitespace-only style strings (e.g. ``"   "``) take the
        identity branch — a persona's ``visual_style`` is only
        considered "set" when it carries non-whitespace content.
        Trailing whitespace + trailing period(s) on ``prompt`` are
        stripped before the suffix so the merged string reads
        ``"a cat, in the style of watercolour"`` rather than
        ``"a cat ., in the style of watercolour"``.
    """
    if style is None or not style.strip():
        return prompt
    if _user_specified_style(prompt):
        return prompt
    cleaned = prompt.rstrip()
    # Strip a single trailing period if present (research §3.4 case
    # 4); leave internal punctuation alone. Re-strip whitespace after
    # the period removal so ``"a cat .  "`` collapses to ``"a cat"``.
    if cleaned.endswith("."):
        cleaned = cleaned[:-1].rstrip()
    return f"{cleaned}, in the style of {style.strip()}"


def _user_specified_style(prompt: str) -> bool:
    """Detect whether the prompt already specifies an explicit style.

    Three deterministic heuristics fire (D-15-4 sub-rule). Each is
    case-insensitive and operates on the lowercased prompt.

    1. Substring ``"in the style of"`` anywhere in the prompt.
    2. ``"as a <modifier> <medium-noun>"`` where ``<medium-noun>`` is
       one of :data:`_AS_A_MEDIUM_NOUNS` (``painting``, ``sketch``,
       ``render``, ``illustration``, ``drawing``, ``photo``,
       ``photograph``) and ``<modifier>`` is any single token. The
       window between ``"as a"`` and the medium noun spans at most
       three tokens (a slack ample for ``"as a watercolour
       painting"`` or ``"as a 3d render"``).
    3. Tail-position adjective: the last five whitespace-separated
       tokens of the prompt contain any entry from
       :data:`_KNOWN_STYLE_TAIL`. Multi-word entries
       (``"oil painting"``, ``"pixel art"``, ``"3d render"``,
       ``"low poly"``, ``"concept art"``) are matched against the
       lowercased prompt as substrings within the tail window.

    The detection is conservative-toward-leaving-the-user-prompt-alone:
    false-positives (style detected when not intended) merely suppress
    the persona style for that prompt — harmless. False-negatives
    (style not detected → persona style applied over user intent) are
    the failure mode T19 verifies empirically.

    Args:
        prompt: The user prompt.

    Returns:
        ``True`` if any of the three heuristics fire; ``False``
        otherwise.
    """
    lowered = prompt.lower()

    # Heuristic 1: explicit "in the style of" substring.
    if "in the style of" in lowered:
        return True

    # Tokens: whitespace-split, lowercase. Used by heuristics 2 + 3.
    tokens = lowered.split()

    # Heuristic 2: "as a <modifier> <medium-noun>" sliding window.
    # Find every "as a" / "as an" anchor, then look at the next few
    # tokens for one of the medium nouns. The window spans up to three
    # tokens past the article so ``"as a hand-drawn pencil sketch"``
    # fires (modifier1 modifier2 medium).
    for i, token in enumerate(tokens):
        if token == "as" and i + 1 < len(tokens) and tokens[i + 1] in {"a", "an"}:
            # Look ahead up to three tokens past "a"/"an".
            for j in range(i + 2, min(i + 5, len(tokens))):
                # Strip trailing punctuation off the candidate noun
                # so ``"as a watercolour painting."`` still fires.
                candidate = tokens[j].rstrip(".,;:!?")
                if candidate in _AS_A_MEDIUM_NOUNS:
                    return True

    # Heuristic 3: tail-position adjective from the closed set.
    # Multi-word entries need substring matching over the tail window;
    # single-word entries match against the tail tokens directly.
    if tokens:
        tail_tokens = tokens[-5:]
        tail_window = " ".join(tail_tokens)
        for entry in _KNOWN_STYLE_TAIL:
            if " " in entry:
                # Multi-word entry — substring match within the tail
                # window string. ``"oil painting"`` in ``"a cat as an
                # oil painting"`` fires here even though heuristic 2
                # already caught it (idempotent overlap is fine).
                if entry in tail_window:
                    return True
            else:
                # Single-word entry — direct token match (strip
                # trailing punctuation off the tail tokens so
                # ``"a cat, watercolour."`` fires).
                for tail_token in tail_tokens:
                    if tail_token.rstrip(".,;:!?") == entry:
                        return True

    return False
