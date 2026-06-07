"""Categorical hard-line filter for the image-generation tool (Spec 15 T09).

This module implements the structural defence layer 3 named in
``decisions.md`` D-15-X-hard-line-filter and ``research.md`` §5: a
pre-dispatch lexical two-set co-occurrence matcher that runs at the
``generate_image`` tool boundary BEFORE the provider call.  The filter is
conservative-by-design — false-positives are an accepted policy cost
(legitimate pediatric-medical, art-history, sex-education prompts will
fire); false-negatives are not.

The function :func:`is_hard_line_violation` returns
``(True, "c1" | "c2" | "c3")`` on trigger and ``(False, None)`` otherwise.
On trigger the calling tool factory MUST emit a
``ToolAuditEvent(metadata={"outcome": "content_rejected_hard_line",
"category": "<cN>", "prompt_sha256": hash_prompt_for_audit(prompt)})``
and return a ``ToolResult(is_error=True,
content="content_rejected_hard_line")``.  **The triggering prompt is
NEVER persisted** — only its sha256 hash via
:func:`hash_prompt_for_audit`.

Layered with provider moderation, never replaces it.  The filter runs
*before* provider moderation; provider moderation stays enabled.

Algorithm (per ``research.md`` §5.3):

1. **Normalise** — Unicode NFKD; strip combining marks; map confusables
   to ASCII via a small explicit table; strip zero-width characters;
   lowercase; collapse ``\\s\\.\\-_·`` runs INSIDE alphabetic sequences
   (so ``c.h.i.l.d`` collapses to ``child``); collapse residual
   whitespace.
2. **Leet-fold inside alphabetic runs only** — substitution table
   (``4→a, 3→e, 1→i, 0→o, 5→s, 7→t, $→s, @→a``) applied only when at
   least one alpha character neighbours the digit *within the same
   token* (D-15-X-hard-line-filter sub-rule 2; protects legitimate
   integers used by the C2 numeric-age scan).
3. **Tokenise** — split on Unicode-aware ``\\W+`` via the ``regex``
   third-party package.
4. **C3 (developmental-stage) test** — if ``tokens ∩
   DEVELOPMENTAL_SET`` is non-empty AND ``tokens ∩ SEX_SET`` is
   non-empty → ``(True, "c3")``.  Checked before C1 because
   developmental-stage tokens are a categorically narrower hit.
5. **C1 (general minor reference) test** — if ``tokens ∩ MINOR_SET``
   is non-empty AND ``tokens ∩ SEX_SET`` is non-empty → ``(True,
   "c1")``.
6. **C2 (numeric-age window) test** — scan the *original* (pre-leet-
   fold) token stream for any token whose numeric prefix is in
   ``[0, 17]``; if any ``SEX_SET`` token appears within an 8-token
   window → ``(True, "c2")``.
7. Otherwise → ``(False, None)``.

References:
    docs/specs/phase2/spec_15/decisions.md D-15-X-hard-line-filter;
    docs/specs/phase2/spec_15/research.md §5.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Final, Literal

import regex as _regex

__all__ = [
    "hash_prompt_for_audit",
    "is_hard_line_violation",
    "leet_fold_inside_alpha",
    "normalise",
    "tokenise",
]


# ---------------------------------------------------------------------------
# Closed lexicons.
#
# DO NOT EXTEND THESE SETS WITHOUT SECURITY REVIEW.
#
# These sets enumerate the trigger surface for the categorical hard line.
# Extending the set broadens the refusal floor; shrinking it narrows it.
# Both directions need deliberate consideration in security review per
# D-15-X-hard-line-filter; the six-month review cadence is itself a
# Phase 6 candidate disposition (research.md §5.5 item 4).
# ---------------------------------------------------------------------------

# DO NOT EXTEND WITHOUT SECURITY REVIEW.
_MINOR_SET: Final[frozenset[str]] = frozenset(
    {
        "child",
        "children",
        "kid",
        "kids",
        "minor",
        "minors",
        "boy",
        "boys",
        "girl",
        "girls",
        "underage",
        "adolescent",
        "adolescents",
        "teen",
        "teens",
        "teenager",
        "teenagers",
        "youth",
        "juvenile",
        "schoolboy",
        "schoolgirl",
    }
)

# DO NOT EXTEND WITHOUT SECURITY REVIEW.
#
# DEVELOPMENTAL_SET is checked *before* MINOR_SET so prompts that hit a
# developmental-stage token (the narrower category) get classified as c3
# rather than the broader c1.  The "pediatric"/"paediatric" tokens live
# here so the B5 false-positive zone in T09's adversarial corpus fires
# under c3 — accepted policy per research §5.2 + §7 risk #10.
_DEVELOPMENTAL_SET: Final[frozenset[str]] = frozenset(
    {
        "preadolescent",
        "prepubescent",
        "preteen",
        "preteens",
        "toddler",
        "toddlers",
        "infant",
        "infants",
        "baby",
        "babies",
        "newborn",
        "newborns",
        "pediatric",
        "paediatric",
    }
)

# DO NOT EXTEND WITHOUT SECURITY REVIEW.
_SEX_SET: Final[frozenset[str]] = frozenset(
    {
        "nude",
        "naked",
        "sexy",
        "sexual",
        "erotic",
        "explicit",
        "lewd",
        "pornographic",
        "porn",
        "intimate",
        "sensual",
        "seductive",
        "arousing",
        "breast",
        "breasts",
    }
)


# ---------------------------------------------------------------------------
# Normalisation tables.
# ---------------------------------------------------------------------------

# Confusable-to-ASCII map.  Kept deliberately small per
# D-15-X-hard-line-filter — only the high-prevalence visual confusables
# from the public obfuscation surveys cited in research §5.1 (Cyrillic
# а/о/е/р/с/х; Greek α/ο; ascii-equivalent fullwidth digits handled by
# NFKD).  Extending the table is reviewable but tightly scoped.
_CONFUSABLES: Final[dict[str, str]] = {
    # Cyrillic lowercase that look like Latin lowercase.
    "а": "a",  # CYRILLIC SMALL LETTER A
    "е": "e",  # CYRILLIC SMALL LETTER IE
    "о": "o",  # CYRILLIC SMALL LETTER O
    "р": "p",  # CYRILLIC SMALL LETTER ER
    "с": "c",  # CYRILLIC SMALL LETTER ES
    "х": "x",  # CYRILLIC SMALL LETTER HA
    "у": "y",  # CYRILLIC SMALL LETTER U
    "л": "l",  # CYRILLIC SMALL LETTER EL (visually like 'л' / 'l')
    # Greek lowercase look-alikes.
    "α": "a",  # GREEK SMALL LETTER ALPHA
    "ο": "o",  # GREEK SMALL LETTER OMICRON
    "ε": "e",  # GREEK SMALL LETTER EPSILON
}

# Zero-width characters stripped during normalisation.  Per research §5
# obfuscation catalogue.
_ZERO_WIDTH: Final[frozenset[str]] = frozenset(
    {
        "​",  # ZERO WIDTH SPACE
        "‌",  # ZERO WIDTH NON-JOINER
        "‍",  # ZERO WIDTH JOINER
        "﻿",  # ZERO WIDTH NO-BREAK SPACE (BOM)
    }
)

# Leet substitution table.  Applied only inside alphabetic runs per
# D-15-X-hard-line-filter sub-rule 2.  '1' maps to 'i' rather than 'l'
# (both are common); 'l' substitutions get caught by the confusable
# table if needed (Cyrillic 'л').
_LEET_TABLE: Final[dict[str, str]] = {
    "4": "a",
    "3": "e",
    "1": "i",
    "0": "o",
    "5": "s",
    "7": "t",
    "$": "s",
    "@": "a",
}

# Regex matching a run of two or more single Latin letters separated by
# dots / dashes / underscores / middle-dots (``c.h.i.l.d``).  The
# match is replaced by the concatenation of its letters, so
# ``c.h.i.l.d`` collapses to ``child``.  Bounded by non-letter
# characters on both sides so legitimate words (``e.g.``) are not
# collapsed unless they form a full letter-by-letter run.
_DOTTED_LETTERS_RE: Final[_regex.Pattern[str]] = _regex.compile(
    r"(?<![a-z])(?:[a-z][.\-_·])+[a-z](?![a-z])",
)

# Regex matching a run of two or more single Latin letters separated by
# whitespace (``t e e n``).  Bounded by non-letter characters on both
# sides.  The match is replaced by the concatenation of its letters.
_SPACED_LETTERS_RE: Final[_regex.Pattern[str]] = _regex.compile(
    r"(?<![a-z])(?:[a-z]\s)+[a-z](?![a-z])",
)


def _collapse_letter_runs(text: str) -> str:
    """Collapse ``c.h.i.l.d`` / ``c h i l d`` letter runs to ``child``.

    Each match is a sequence of single Latin letters separated by
    either dotted-style separators (``.`` ``-`` ``_`` ``·``) or
    whitespace; the match is replaced by its letters concatenated.
    Bounded by non-letter characters on both sides so multi-letter
    words are not eaten.
    """
    dotted: str = _DOTTED_LETTERS_RE.sub(
        lambda m: "".join(c for c in m.group(0) if c.isalpha()),
        text,
    )
    result: str = _SPACED_LETTERS_RE.sub(
        lambda m: "".join(c for c in m.group(0) if c.isalpha()),
        dotted,
    )
    return result


# Regex used by ``tokenise`` to split on non-word characters
# (Unicode-aware via the ``regex`` package, which provides full
# ``\p{...}`` support that stdlib ``re`` lacks).
_TOKEN_SPLIT_RE: Final[_regex.Pattern[str]] = _regex.compile(r"\W+", flags=_regex.UNICODE)

# Window size (in token offset units) used by the C2 numeric-age scan.
_C2_WINDOW: Final[int] = 8

# Numeric-age range that triggers C2 (inclusive on both ends).
_C2_AGE_MIN: Final[int] = 0
_C2_AGE_MAX: Final[int] = 17


# ---------------------------------------------------------------------------
# Public helpers.
# ---------------------------------------------------------------------------


def normalise(prompt: str) -> str:
    """Apply the obfuscation-resistant normalisation pipeline.

    Steps (in order):

    1. Strip zero-width characters (``ZWSP``, ``ZWNJ``, ``ZWJ``,
       ``BOM``).
    2. Unicode NFKD normalisation (decomposes fullwidth /
       mathematical-alphanumeric / combining-mark forms to base + mark
       sequences).
    3. Strip combining marks (Unicode category ``Mn``).
    4. Lowercase.
    5. Map visual confusables to ASCII via ``_CONFUSABLES``.
    6. Apply leet-fold inside alphabetic runs (delegated to
       :func:`leet_fold_inside_alpha`).
    7. Collapse runs of ``\\s\\.\\-_·`` between Latin letters (the
       ``c.h.i.l.d`` → ``child`` rule).
    8. Collapse any remaining whitespace run to a single space.

    Args:
        prompt: The raw prompt string.

    Returns:
        The normalised string ready for :func:`tokenise`.
    """
    # 1. Strip zero-width characters BEFORE NFKD so they cannot survive
    #    decomposition (some zero-width forms have NFKD targets that
    #    aren't always trimmed cleanly).
    if any(c in prompt for c in _ZERO_WIDTH):
        prompt = "".join(c for c in prompt if c not in _ZERO_WIDTH)

    # 2 + 3. NFKD then strip combining marks.
    decomposed = unicodedata.normalize("NFKD", prompt)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")

    # 4. Lowercase.
    lowered = stripped.lower()

    # 5. Map confusables.
    if any(c in lowered for c in _CONFUSABLES):
        lowered = "".join(_CONFUSABLES.get(c, c) for c in lowered)

    # 6. Leet-fold inside alphabetic runs.
    leet_folded = leet_fold_inside_alpha(lowered)

    # 7. Collapse dotted / spaced single-letter sequences
    #    (``c.h.i.l.d`` → ``child``; ``t e e n`` → ``teen``).
    collapsed = _collapse_letter_runs(leet_folded)

    # 8. Collapse residual whitespace runs to a single space.
    return " ".join(collapsed.split())


def leet_fold_inside_alpha(prompt: str) -> str:
    """Fold leet substitutions only inside alphabetic runs.

    A leet digit or symbol is folded to its alphabetic counterpart when
    at least one neighbouring character WITHIN THE SAME WORD-RUN is a
    Latin letter.  Pure numeric tokens like ``"15 year old"`` are left
    untouched so the C2 numeric-age scan keeps working.

    Args:
        prompt: The (typically already-lowercased) input string.

    Returns:
        The string with in-word leet characters substituted via
        ``_LEET_TABLE``.
    """
    chars = list(prompt)
    n = len(chars)
    # Walk the string and replace any leet character that has an alpha
    # neighbour (left or right) within the same word (no whitespace
    # between).  This treats "3rotic" as alpha-context (the '3' has 'r'
    # to its right) while leaving "3 cats" alone.
    for i, ch in enumerate(chars):
        if ch not in _LEET_TABLE:
            continue
        # Left neighbour: an alpha within the same word-run.
        left_is_alpha = False
        j = i - 1
        while j >= 0 and not chars[j].isspace():
            if chars[j].isalpha():
                left_is_alpha = True
                break
            j -= 1
        # Right neighbour: an alpha within the same word-run.
        right_is_alpha = False
        k = i + 1
        while k < n and not chars[k].isspace():
            if chars[k].isalpha():
                right_is_alpha = True
                break
            k += 1
        if left_is_alpha or right_is_alpha:
            chars[i] = _LEET_TABLE[ch]
    return "".join(chars)


def tokenise(prompt: str) -> list[str]:
    """Split a (typically already-normalised) prompt into tokens.

    Splits on Unicode-aware ``\\W+`` via the third-party ``regex``
    package.  Empty tokens (from leading / trailing / consecutive
    separators) are dropped.

    Args:
        prompt: The input string.

    Returns:
        The token list.
    """
    return [tok for tok in _TOKEN_SPLIT_RE.split(prompt) if tok]


def hash_prompt_for_audit(prompt: str) -> str:
    """Return the sha256 hex digest of the prompt for audit logging.

    The categorical hard-line filter NEVER persists the triggering
    prompt itself — only this hash, via
    ``ToolAuditEvent.metadata["prompt_sha256"]``.

    Args:
        prompt: The triggering prompt.

    Returns:
        64-character lowercase hex string.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# C2 numeric-age helper.
# ---------------------------------------------------------------------------


def _extract_leading_int(token: str) -> int | None:
    """Return the leading integer of ``token`` or ``None`` if absent.

    ``"15"`` → 15; ``"16th"`` → 16; ``"abc"`` → ``None``; ``"3.5"`` → 3.

    The C2 numeric-age scan uses leading-integer extraction so common
    ordinal/century forms (``"16th"``, ``"17th"``) and bare integers
    both surface ages cleanly.
    """
    if not token:
        return None
    if not token[0].isdigit():
        return None
    digits: list[str] = []
    for c in token:
        if c.isdigit():
            digits.append(c)
        else:
            break
    try:
        return int("".join(digits))
    except ValueError:  # pragma: no cover — defensive
        return None


def _c2_numeric_age_window(tokens: list[str]) -> bool:
    """Return True when a numeric-age token co-occurs with a SEX token.

    Scans ``tokens`` for any token whose leading integer is in
    ``[_C2_AGE_MIN, _C2_AGE_MAX]``; for each such index, returns True
    if any token within ``±_C2_WINDOW`` is in ``_SEX_SET``.
    """
    n = len(tokens)
    for i, token in enumerate(tokens):
        age = _extract_leading_int(token)
        if age is None or age < _C2_AGE_MIN or age > _C2_AGE_MAX:
            continue
        lo = max(0, i - _C2_WINDOW)
        hi = min(n, i + _C2_WINDOW + 1)
        for j in range(lo, hi):
            if j == i:
                continue
            if tokens[j] in _SEX_SET:
                return True
    return False


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


_Category = Literal["c1", "c2", "c3"]


def is_hard_line_violation(prompt: str) -> tuple[bool, _Category | None]:
    """Decide whether ``prompt`` trips the categorical hard line.

    Pre-dispatch matcher; runs at the ``generate_image`` tool boundary
    BEFORE any provider call.  Conservative-by-design: false-positives
    (legitimate pediatric-medical, art-history, sex-education prompts)
    are an accepted policy cost; false-negatives are not.

    Args:
        prompt: The user prompt about to be sent to the image backend.
            Empty / whitespace-only / ``None`` are treated as not
            tripping the filter (the upstream tool factory validates
            the prompt is non-empty before calling).

    Returns:
        ``(True, "c1" | "c2" | "c3")`` when the filter trips, where
        ``cN`` is the catalogue category from ``research.md`` §5.2;
        ``(False, None)`` otherwise.  See the module docstring for the
        decision order between C1, C2, and C3.
    """
    if not prompt or not prompt.strip():
        return False, None

    normalised = normalise(prompt)
    # Tokens of the normalised (and leet-folded, because ``normalise``
    # already folds) text — used for C1 / C3.
    folded_tokens = tokenise(normalised)
    folded_set = set(folded_tokens)

    # C3: developmental-stage tokens co-occurring with sex tokens
    # (narrower category, checked first).
    if (folded_set & _DEVELOPMENTAL_SET) and (folded_set & _SEX_SET):
        return True, "c3"

    # C1: any minor-reference token co-occurring with a sex token.
    if (folded_set & _MINOR_SET) and (folded_set & _SEX_SET):
        return True, "c1"

    # C2: numeric-age window scan.  Runs on the ORIGINAL (pre-leet-fold)
    # token stream so legitimate integer tokens like "16", "17" survive
    # — the leet-fold step would corrupt them.  We reconstruct the
    # pre-leet-fold normalised tokens by repeating steps 1-5 + 7-8 of
    # ``normalise`` (skipping leet-fold) inline.
    pre_leet = _normalise_without_leet(prompt)
    pre_leet_tokens = tokenise(pre_leet)
    if _c2_numeric_age_window(pre_leet_tokens):
        return True, "c2"

    return False, None


def _normalise_without_leet(prompt: str) -> str:
    """Replicate :func:`normalise` minus the leet-fold step.

    Used by the C2 numeric-age scan so legitimate integer tokens
    (``"16"`` / ``"17"`` / ``"15"``) survive the pipeline — the
    leet-fold step in :func:`normalise` would substitute ``1→i``,
    ``0→o`` etc., corrupting the age window scan.
    """
    if any(c in prompt for c in _ZERO_WIDTH):
        prompt = "".join(c for c in prompt if c not in _ZERO_WIDTH)
    decomposed = unicodedata.normalize("NFKD", prompt)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    lowered = stripped.lower()
    if any(c in lowered for c in _CONFUSABLES):
        lowered = "".join(_CONFUSABLES.get(c, c) for c in lowered)
    collapsed = _collapse_letter_runs(lowered)
    return " ".join(collapsed.split())
