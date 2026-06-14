"""Unit tests for the TurnTakingController endpointing policy (spec V4 T02).

All tests use an injected clock (a fixed base instant + explicit deltas) so
the silence-threshold logic is fully deterministic — no ``datetime.now()``
reads anywhere in the decision path (D-V4-1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from persona_voice.stt.types import SpeechEndedEvent
from persona_voice.turn_taking.controller import (
    TurnTakingController,
    TurnVerdict,
)

_BASE = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


def _offset(
    *,
    corroborates: bool = False,
    transcript_settled: bool = False,
    source: str = "silero",
) -> SpeechEndedEvent:
    return SpeechEndedEvent(
        ts_audio_s=1.0,
        ts_emit=_BASE,
        source=source,  # type: ignore[arg-type]
        corroborates=corroborates,
        transcript_settled=transcript_settled,
    )


def _at(ms: float) -> datetime:
    """The instant ``ms`` after the offset event."""
    return _BASE + timedelta(milliseconds=ms)


# ---------- still speaking ---------------------------------------------------


def test_no_offset_means_user_still_speaking_waits() -> None:
    ctrl = TurnTakingController()
    d = ctrl.decide_turn_end(last_offset=None, settled_text="hello", now=_BASE)
    assert d.verdict is TurnVerdict.WAIT
    assert d.reason == "no_offset_user_still_speaking"


# ---------- the canonical END_TURN ------------------------------------------


def test_silence_past_threshold_ends_turn() -> None:
    """700 ms+ of uncorroborated silence with a complete utterance → END_TURN."""
    ctrl = TurnTakingController(silence_threshold_ms=700.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(),
        settled_text="that is all I wanted to say",
        now=_at(750.0),
    )
    assert d.verdict is TurnVerdict.END_TURN
    assert d.reason == "silence_threshold"
    assert d.silence_elapsed_ms == pytest.approx(750.0)


def test_corroborated_offset_ends_turn_faster() -> None:
    """A provider-corroborated offset shortens the wait to 300 ms (D-V4-1)."""
    ctrl = TurnTakingController(silence_threshold_ms=700.0, corroborated_silence_threshold_ms=300.0)
    # 350 ms: below the bare threshold, above the corroborated one.
    d = ctrl.decide_turn_end(
        last_offset=_offset(corroborates=True),
        settled_text="done",
        now=_at(350.0),
    )
    assert d.verdict is TurnVerdict.END_TURN
    assert d.reason == "corroborated_silence"


def test_transcript_settled_also_corroborates() -> None:
    ctrl = TurnTakingController(corroborated_silence_threshold_ms=300.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(transcript_settled=True),
        settled_text="ok",
        now=_at(310.0),
    )
    assert d.verdict is TurnVerdict.END_TURN
    assert d.reason == "corroborated_silence"


# ---------- WAIT below threshold (mid-thought pause) ------------------------


def test_silence_below_threshold_waits() -> None:
    """A brief pause shorter than the threshold → WAIT (criterion #2)."""
    ctrl = TurnTakingController(silence_threshold_ms=700.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(),
        settled_text="I was thinking we could go",
        now=_at(200.0),
    )
    assert d.verdict is TurnVerdict.WAIT
    assert d.reason == "silence_below_threshold"


# ---------- the textual-completion hold-list gate (D-V4-1, criterion #2) ----


def test_trailing_conjunction_holds_the_floor_past_threshold() -> None:
    """Even past the silence threshold, a mid-thought trailing token WAITS —
    this is what buys criterion #2 without a semantic model."""
    ctrl = TurnTakingController(silence_threshold_ms=700.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(),
        settled_text="I was thinking, maybe we could go and",
        now=_at(900.0),  # well past threshold
    )
    assert d.verdict is TurnVerdict.WAIT
    assert d.reason == "mid_thought_hold_token"


def test_trailing_filler_holds_the_floor() -> None:
    ctrl = TurnTakingController(silence_threshold_ms=700.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(),
        settled_text="the answer is um",
        now=_at(900.0),
    )
    assert d.verdict is TurnVerdict.WAIT
    assert d.reason == "mid_thought_hold_token"


def test_hold_token_holds_even_when_corroborated() -> None:
    """The textual gate beats corroboration — a mid-thought is a mid-thought
    no matter how settled the (incomplete) transcript is."""
    ctrl = TurnTakingController(corroborated_silence_threshold_ms=300.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(corroborates=True, transcript_settled=True),
        settled_text="I want to go to",
        now=_at(500.0),
    )
    assert d.verdict is TurnVerdict.WAIT
    assert d.reason == "mid_thought_hold_token"


def test_trailing_punctuation_does_not_hide_a_hold_token() -> None:
    """A comma/ellipsis after the hold token must not defeat the gate."""
    ctrl = TurnTakingController()
    d = ctrl.decide_turn_end(
        last_offset=_offset(),
        settled_text="well, I think, but...",
        now=_at(900.0),
    )
    assert d.verdict is TurnVerdict.WAIT
    assert d.reason == "mid_thought_hold_token"


def test_complete_sentence_not_held() -> None:
    """A genuinely complete utterance ends the turn even though it contains
    hold-words mid-sentence — only the FINAL token matters."""
    ctrl = TurnTakingController(silence_threshold_ms=700.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(),
        settled_text="I went to the store and bought milk",
        now=_at(800.0),
    )
    assert d.verdict is TurnVerdict.END_TURN


def test_empty_settled_text_does_not_crash_and_uses_silence() -> None:
    # Corroborated so the conservative no-transcript guard (T09) doesn't apply —
    # this exercises the pure silence-threshold path with empty text.
    ctrl = TurnTakingController(corroborated_silence_threshold_ms=300.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(corroborates=True), settled_text="", now=_at(800.0)
    )
    assert d.verdict is TurnVerdict.END_TURN


def test_none_settled_text_uses_silence_only() -> None:
    ctrl = TurnTakingController(corroborated_silence_threshold_ms=300.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(corroborates=True), settled_text=None, now=_at(800.0)
    )
    assert d.verdict is TurnVerdict.END_TURN


# ---------- custom hold-list -----------------------------------------------


def test_custom_hold_tokens_override() -> None:
    ctrl = TurnTakingController(hold_tokens=frozenset({"zzz"}))
    # "and" is no longer a hold token with the custom list → ends the turn.
    d = ctrl.decide_turn_end(last_offset=_offset(), settled_text="cats and", now=_at(900.0))
    assert d.verdict is TurnVerdict.END_TURN
    # "zzz" now holds.
    d2 = ctrl.decide_turn_end(last_offset=_offset(), settled_text="cats zzz", now=_at(900.0))
    assert d2.verdict is TurnVerdict.WAIT
