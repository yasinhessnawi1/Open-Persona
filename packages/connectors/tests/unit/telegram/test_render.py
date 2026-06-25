"""Outbound rendering + splitting (Spec C2 T3) — pure, the C3/C4 reference.

Tests the two research-flagged invariants: the splitter counts UTF-16 units (not
chars), breaks on natural boundaries never mid-word/mid-surrogate, and splits the
plaintext THEN wraps HTML so a tag never tears across a boundary (D-C2-3); the
bold ``<b>name</b>`` header lands on the first part only, with name + body
HTML-escaped (D-C2-5).
"""

from __future__ import annotations

import pytest
from persona.schema.origination import PersonaIdentityTag
from persona_connectors.telegram.render import (
    escape_html,
    render_outbound,
    split_text,
    utf16_length,
)

_PERSONA = PersonaIdentityTag(persona_id="p1", display_name="Astrid", visual_ref=None)


# --- utf16_length ---


def test_utf16_length_counts_astral_as_two() -> None:
    """An emoji (astral-plane) is one code point but two UTF-16 units (Telegram's unit)."""
    assert utf16_length("ab") == 2
    assert utf16_length("😀") == 2  # U+1F600 — a surrogate pair
    assert utf16_length("a😀b") == 4


# --- escape_html ---


def test_escape_html_escapes_the_three_telegram_chars() -> None:
    """Only & < > are escaped (ampersand first), per Telegram HTML."""
    assert escape_html("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_escape_html_ampersand_first_avoids_double_escape() -> None:
    """An existing entity-looking sequence is escaped once, correctly."""
    assert escape_html("<b>") == "&lt;b&gt;"


# --- split_text ---


def test_short_text_is_one_chunk() -> None:
    assert split_text("hello world", budget=100) == ["hello world"]


def test_split_breaks_on_paragraph_then_line_then_sentence_then_word() -> None:
    """Greedy split prefers the largest chunk ending on the strongest boundary."""
    # Paragraph boundary preferred.
    text = "alpha beta\n\ngamma delta"
    assert split_text(text, budget=12) == ["alpha beta", "gamma delta"]


def test_split_breaks_on_sentence_boundary() -> None:
    # budget 12 fits "Three four." (11 units) whole, so the break is the sentence,
    # not a word — proving the sentence boundary is preferred over a word break.
    text = "One two. Three four."
    parts = split_text(text, budget=12)
    assert parts == ["One two.", "Three four."]


def test_split_never_breaks_mid_word() -> None:
    text = "alpha beta gamma delta"
    parts = split_text(text, budget=12)
    # Every part is whole words — no fragment is a prefix-cut of a source word.
    for part in parts:
        for word in part.split():
            assert word in {"alpha", "beta", "gamma", "delta"}
    assert "".join(p.replace(" ", "") for p in parts) == text.replace(" ", "")


def test_split_respects_utf16_budget() -> None:
    """Every chunk is within budget measured in UTF-16 units (emoji counts as 2)."""
    text = "😀 😀 😀 😀 😀"  # five emoji (2 units each) + spaces
    parts = split_text(text, budget=5)
    assert parts  # produced something
    for part in parts:
        assert utf16_length(part) <= 5


def test_split_does_not_tear_a_surrogate_pair() -> None:
    """A hard-wrap of emoji-only text never splits mid-surrogate (chunks stay valid)."""
    text = "😀😀😀😀"  # no boundaries → hard-wrapped; 8 UTF-16 units total
    parts = split_text(text, budget=3)  # 3 units → one emoji (2) per chunk
    assert "".join(parts) == text
    for part in parts:
        part.encode("utf-16")  # would raise if a lone surrogate slipped through


def test_split_hard_wraps_an_oversized_token() -> None:
    """A single word longer than budget is hard-wrapped (last resort), losing nothing."""
    parts = split_text("supercalifragilistic", budget=5)
    assert "".join(parts) == "supercalifragilistic"
    assert all(utf16_length(p) <= 5 for p in parts)


def test_split_first_budget_is_smaller() -> None:
    """The first chunk honours a reduced first_budget (header reserve)."""
    parts = split_text("aaaa bbbb cccc", budget=9, first_budget=4)
    assert utf16_length(parts[0]) <= 4


def test_split_empty_text_is_empty_list() -> None:
    assert split_text("", budget=100) == []
    assert split_text("   ", budget=100) == []


def test_split_rejects_nonpositive_budget() -> None:
    with pytest.raises(ValueError, match="budget"):
        split_text("x", budget=0)


# --- render_outbound ---


def test_render_short_reply_has_bold_header_then_body() -> None:
    """The bold name header precedes the escaped body (D-C2-5)."""
    parts = render_outbound(_PERSONA, "hello there", budget=100)
    assert parts == ["<b>Astrid</b>\nhello there"]


def test_render_escapes_body_and_name() -> None:
    """Body + name are HTML-escaped; only the header's own <b> tags are literal."""
    persona = PersonaIdentityTag(persona_id="p", display_name="A<b>", visual_ref=None)
    parts = render_outbound(persona, "1 < 2 & 3 > 0", budget=100)
    assert parts == ["<b>A&lt;b&gt;</b>\n1 &lt; 2 &amp; 3 &gt; 0"]


def test_render_header_on_first_part_only() -> None:
    """A long reply splits into multiple parts; only the first carries the header."""
    body = "alpha beta gamma delta epsilon zeta eta theta"
    parts = render_outbound(_PERSONA, body, budget=20)
    assert len(parts) > 1
    assert parts[0].startswith("<b>Astrid</b>\n")
    for continuation in parts[1:]:
        assert "<b>Astrid</b>" not in continuation


def test_render_html_tag_never_torn_across_parts() -> None:
    """Because we split plaintext THEN escape, no part contains a half entity/tag."""
    body = "a < b & c > d " * 10  # lots of escapables, forces splitting
    parts = render_outbound(_PERSONA, body, budget=24)
    assert len(parts) > 1
    for part in parts:
        # A well-formed part has balanced &…; entities — no dangling '&' without ';'.
        for piece in part.split("&")[1:]:
            assert ";" in piece  # every '&' starts a complete entity


def test_render_empty_body_is_header_only() -> None:
    """An empty reply still renders a (header-only) message, never a crash."""
    assert render_outbound(_PERSONA, "", budget=100) == ["<b>Astrid</b>"]
