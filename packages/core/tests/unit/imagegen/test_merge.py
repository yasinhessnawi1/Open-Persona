"""Tests for ``persona.imagegen._merge`` — visual_style merge (spec 15 T11).

Deterministic mechanics only. Empirical assertions (model-behaviour
"watercolour cat is a recognisable cat") live in T19's
``@pytest.mark.external`` smoke suite, not here — see
``docs/specs/phase2/spec_15/research.md`` §3.5 + T11's task scope.

The corpus mirrors research.md §3.4 case-for-case plus targeted unit
coverage of the three :func:`_user_specified_style` heuristics and the
closed-set lock on :data:`_KNOWN_STYLE_TAIL`.
"""

from __future__ import annotations

import pytest
from persona.imagegen._merge import (
    _AS_A_MEDIUM_NOUNS,
    _KNOWN_STYLE_TAIL,
    _user_specified_style,
    merge_visual_style,
)


class TestMergeVisualStyleTable:
    """Deterministic mechanics corpus from research.md §3.4."""

    @pytest.mark.parametrize(
        ("prompt", "style", "expected"),
        [
            # Case 1: empty style → identity.
            ("a cat", None, "a cat"),
            # Case 2: whitespace-only style → identity.
            ("a cat", "   ", "a cat"),
            # Case 3: happy path — content-first suffix merge.
            ("a cat", "watercolour", "a cat, in the style of watercolour"),
            # Case 4: trailing period stripped before suffix.
            ("a cat.", "watercolour", "a cat, in the style of watercolour"),
            # Case 5: trailing whitespace stripped before suffix.
            ("a cat   ", "watercolour", "a cat, in the style of watercolour"),
            # Case 6: explicit "in the style of" → identity (user wins).
            (
                "a cat in the style of Van Gogh",
                "minimalist",
                "a cat in the style of Van Gogh",
            ),
            # Case 7: tail-position adjective from the closed set → identity.
            ("a watercolour cat", "photorealistic", "a watercolour cat"),
            # Case 8: long style descriptor passthrough — no truncation.
            (
                "a cat",
                (
                    "warm editorial illustration, muted earth palette, soft "
                    "linework, hand-drawn feel, golden-hour lighting, hand "
                    "set against soft daylight"
                ),
                (
                    "a cat, in the style of warm editorial illustration, muted "
                    "earth palette, soft linework, hand-drawn feel, golden-hour "
                    "lighting, hand set against soft daylight"
                ),
            ),
            # Case 9: non-English style descriptor.
            (
                "a cat",
                "akvarell, dempete farger",
                "a cat, in the style of akvarell, dempete farger",
            ),
            # Case 10: multi-line prompt preserves newline.
            (
                "a cat\nsitting on a chair",
                "watercolour",
                "a cat\nsitting on a chair, in the style of watercolour",
            ),
        ],
    )
    def test_research_corpus(self, prompt: str, style: str | None, expected: str) -> None:
        assert merge_visual_style(prompt, style) == expected


class TestMergeVisualStyleBranches:
    """Branch coverage for :func:`merge_visual_style` independent of corpus."""

    def test_none_style_returns_prompt_object(self) -> None:
        # Identity branch returns the original string object (no copy).
        prompt = "anything goes here"
        assert merge_visual_style(prompt, None) is prompt

    def test_empty_string_style_returns_prompt(self) -> None:
        prompt = "a sunny mountain"
        assert merge_visual_style(prompt, "") is prompt

    def test_whitespace_only_style_returns_prompt(self) -> None:
        prompt = "a sunny mountain"
        # Mix of spaces / tabs / newlines — all treated as "no style".
        assert merge_visual_style(prompt, " \t \n ") is prompt

    def test_style_is_strip_applied(self) -> None:
        # Surrounding whitespace on the style is stripped in the suffix.
        result = merge_visual_style("a cat", "  watercolour  ")
        assert result == "a cat, in the style of watercolour"

    def test_user_specified_style_returns_prompt_object(self) -> None:
        prompt = "a cat in the style of Hokusai"
        # Heuristic 1 fires; identity branch returns the original.
        assert merge_visual_style(prompt, "minimalist") is prompt

    def test_multiple_trailing_periods_only_strips_one(self) -> None:
        # We strip a single trailing period (research.md §3.4 case 4).
        # A double-period at the end leaves the inner period in place,
        # which we accept — multi-period prompts are not in the v0.1
        # corpus and would not change downstream behaviour.
        result = merge_visual_style("wait..", "watercolour")
        assert result == "wait., in the style of watercolour"

    def test_internal_period_preserved(self) -> None:
        # Internal punctuation is not touched.
        result = merge_visual_style("a cat. a dog", "watercolour")
        assert result == "a cat. a dog, in the style of watercolour"

    def test_empty_prompt_with_style(self) -> None:
        # An empty prompt is a weird-but-valid input — the merge still
        # appends the suffix; the model surface above (T12) will reject
        # before we ever get here, but the function itself stays total.
        result = merge_visual_style("", "watercolour")
        assert result == ", in the style of watercolour"

    def test_unicode_prompt_passthrough(self) -> None:
        # The function never touches characters; it only strips
        # trailing whitespace + a single trailing period.
        result = merge_visual_style("猫の絵", "watercolour")
        assert result == "猫の絵, in the style of watercolour"


class TestUserSpecifiedStyleHeuristic1:
    """Heuristic 1: substring ``"in the style of"``."""

    @pytest.mark.parametrize(
        "prompt",
        [
            "a cat in the style of Van Gogh",
            "IN THE STYLE OF Monet, please",
            "render this in The Style Of Picasso",
            "lots of detail, in the style of cinematic noir, please",
        ],
    )
    def test_substring_fires(self, prompt: str) -> None:
        assert _user_specified_style(prompt) is True

    @pytest.mark.parametrize(
        "prompt",
        [
            "a cat",
            "in the styles of",  # plural — must not fire
            "the style is sharp",
            "style of cat",
        ],
    )
    def test_substring_does_not_fire(self, prompt: str) -> None:
        assert _user_specified_style(prompt) is False


class TestUserSpecifiedStyleHeuristic2:
    """Heuristic 2: ``"as a <modifier> <medium-noun>"`` window."""

    @pytest.mark.parametrize(
        "prompt",
        [
            "render this as a watercolour painting",
            "draw it as a pencil sketch",
            "imagine the scene as a 3d render",
            "the dog as an oil painting",
            "as a high-detail illustration of a forest",
            "as a candid photo",
            "render as a black-and-white photograph",
        ],
    )
    def test_window_fires(self, prompt: str) -> None:
        assert _user_specified_style(prompt) is True

    @pytest.mark.parametrize(
        "prompt",
        [
            "a cat",
            "as a cat sat there",  # "cat" is not a medium-noun
            "as an apple",  # "apple" is not a medium-noun
            # Medium-noun is too far past "as a" — outside the 3-token slack.
            "as a richly detailed elaborately bordered ornate hand-bound painting",
        ],
    )
    def test_window_does_not_fire(self, prompt: str) -> None:
        assert _user_specified_style(prompt) is False

    def test_trailing_punctuation_on_medium_noun_still_fires(self) -> None:
        # Demonstrates the rstrip on the candidate medium noun.
        assert _user_specified_style("render this as a watercolour painting.") is True


class TestUserSpecifiedStyleHeuristic3:
    """Heuristic 3: tail-position adjective from :data:`_KNOWN_STYLE_TAIL`."""

    @pytest.mark.parametrize(
        "entry",
        sorted(_KNOWN_STYLE_TAIL),
    )
    def test_every_closed_set_entry_fires_at_tail(self, entry: str) -> None:
        # Every entry in the closed 20-entry set fires when present at
        # the end of the prompt. The tail-window matching is the
        # binding contract — multi-word entries use substring match,
        # single-word entries use token-equality.
        prompt = f"a cat {entry}"
        assert _user_specified_style(prompt) is True

    def test_single_word_in_tail_window(self) -> None:
        # "watercolour" lands in the last-5 tokens.
        assert _user_specified_style("a quick sketch of a watercolour") is True

    def test_multi_word_entry_in_tail_window(self) -> None:
        # "oil painting" is a multi-word closed-set entry; substring
        # matching within the tail window catches it.
        assert _user_specified_style("a slow careful oil painting") is True

    def test_outside_tail_window_does_not_fire(self) -> None:
        # "watercolour" early in a long prompt is OUTSIDE the last-5
        # tokens, so heuristic 3 does not fire. Heuristics 1 + 2 also
        # don't fire here (no "in the style of", no "as a <noun>").
        prompt = "watercolour is one of many media but here I want a sunny meadow with daisies"
        assert _user_specified_style(prompt) is False

    def test_punctuation_on_tail_token_still_fires(self) -> None:
        # Trailing punctuation on a tail token is stripped before the
        # closed-set equality check.
        assert _user_specified_style("a quick sketch, watercolour.") is True

    def test_case_insensitive_tail_match(self) -> None:
        # The lowercase happens before heuristic dispatch.
        assert _user_specified_style("a cat WATERCOLOUR") is True

    def test_empty_prompt(self) -> None:
        # No tokens, no heuristics can fire.
        assert _user_specified_style("") is False


class TestClosedSetInvariants:
    """Lock the closed-set surface so accidental drift is caught."""

    def test_known_style_tail_size_matches_enumeration(self) -> None:
        # tasks.md §T11 enumerates 21 entries (both "watercolour" and
        # "watercolor" are listed verbatim — UK + US spellings); the
        # prose around it says "20 entries" but the source-as-truth
        # discipline (CLAUDE.md + decisions.md gate paragraph) means
        # the enumeration wins. Any change should go through code
        # review (per D-15-4 sub-rule + the "do not extend without
        # security review"-class discipline carried into Spec 15).
        assert len(_KNOWN_STYLE_TAIL) == 21

    def test_known_style_tail_exact_membership(self) -> None:
        # Spell out the closed set so accidental rename / typo is
        # caught by the test rather than at code review.
        expected: frozenset[str] = frozenset(
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
        assert expected == _KNOWN_STYLE_TAIL

    def test_known_style_tail_is_frozenset(self) -> None:
        # Immutability is the conventional protection against runtime
        # mutation of the closed set.
        assert isinstance(_KNOWN_STYLE_TAIL, frozenset)

    def test_known_style_tail_entries_are_lowercase(self) -> None:
        # Heuristic 3 lowercases the prompt; the closed set must
        # already be lowercase or matches would silently fail.
        for entry in _KNOWN_STYLE_TAIL:
            assert entry == entry.lower()

    def test_as_a_medium_nouns_is_frozenset(self) -> None:
        assert isinstance(_AS_A_MEDIUM_NOUNS, frozenset)

    def test_as_a_medium_nouns_entries_are_lowercase(self) -> None:
        for entry in _AS_A_MEDIUM_NOUNS:
            assert entry == entry.lower()


class TestMergeVisualStyleConflictResolution:
    """Cross-heuristic conflict cases — user-specified style always wins."""

    def test_explicit_in_the_style_of_wins_over_persona_style(self) -> None:
        # D-15-4 sub-rule: user-specified style detected → identity.
        # Even though a persona style is configured, the prompt's
        # explicit "in the style of Van Gogh" wins.
        result = merge_visual_style(
            "a cat in the style of Van Gogh",
            "dark moody, low-key lighting",
        )
        assert result == "a cat in the style of Van Gogh"

    def test_tail_adjective_wins_over_persona_style(self) -> None:
        result = merge_visual_style("a cat anime", "watercolour")
        assert result == "a cat anime"

    def test_as_a_medium_wins_over_persona_style(self) -> None:
        result = merge_visual_style(
            "render this as a watercolour painting",
            "photorealistic",
        )
        assert result == "render this as a watercolour painting"

    def test_persona_style_applies_when_no_user_style(self) -> None:
        # The other direction: no user style signal → persona style
        # merged in. Locks the "default behaviour" baseline.
        result = merge_visual_style("a sunny meadow", "watercolour")
        assert result == "a sunny meadow, in the style of watercolour"
