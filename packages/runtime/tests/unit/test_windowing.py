"""Long-interaction windowing (Spec K2, T8b; K2-D-5).

Bounds the extraction pass: the compacted summary rides as CONTEXT (explicitly
not-quotable), and only the verbatim tail PAST ``synthesised_up_to`` is the
grounding source — a summary must NEVER become a candidate's grounding. Returns
``None`` when there is nothing new (the idempotency skip).
"""

from __future__ import annotations

from persona.extraction import InteractionKind
from persona_runtime.extraction.windowing import Window, build_window

_MSGS = [
    ("user", "I'm a nurse"),
    ("assistant", "noted"),
    ("user", "and I went vegetarian last year"),
]


def _window(up_to: int, *, summary: str = "") -> Window | None:
    return build_window(
        messages=_MSGS,
        compacted_summary=summary,
        synthesised_up_to=up_to,
        interaction_kind=InteractionKind.CONVERSATION,
        interaction_id="conv-1",
        persona_id="p1",
    )


def test_nothing_new_returns_none() -> None:
    assert _window(len(_MSGS)) is None
    assert _window(len(_MSGS) + 5) is None  # marker ahead (defensive)


def test_high_water_mark_is_the_total_message_count() -> None:
    w = _window(0)
    assert w is not None
    assert w.high_water_mark == len(_MSGS)


def test_only_the_tail_past_the_marker_is_in_the_grounding_transcript() -> None:
    w = _window(2)  # only the 3rd message is new
    assert w is not None
    assert "went vegetarian" in w.input.content
    assert "I'm a nurse" not in w.input.content  # already synthesised — not re-grounded


def test_summary_is_context_and_explicitly_not_quotable() -> None:
    w = _window(2, summary="The user is a nurse.")
    assert w is not None
    assert "The user is a nurse." in w.input.content  # present as context
    low = w.input.content.lower()
    # the summary block is marked do-not-quote; the tail is the quotable source (K2-D-5)
    assert "do not quote" in low or "not quote" in low
    assert "quote evidence only" in low or "quote evidence" in low


def test_no_summary_yields_just_the_transcript() -> None:
    w = _window(0, summary="")
    assert w is not None
    assert "I'm a nurse" in w.input.content
    assert w.input.interaction_id == "conv-1"
    assert w.input.persona_id == "p1"
