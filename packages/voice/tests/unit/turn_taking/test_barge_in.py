"""Unit tests for the BargeInDetector (spec V4 T03)."""

from __future__ import annotations

from datetime import UTC, datetime

from persona_voice.stt.types import SpeechStartedEvent
from persona_voice.turn_taking.barge_in import BargeInDetector, BargeInVerdict
from persona_voice.turn_taking.states import AgentState

_BASE = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


def _onset(*, confidence: float | None = 0.9, source: str = "silero") -> SpeechStartedEvent:
    return SpeechStartedEvent(
        ts_audio_s=5.0,
        ts_emit=_BASE,
        source=source,  # type: ignore[arg-type]
        confidence=confidence,
    )


# ---------- scope gate ------------------------------------------------------


def test_onset_while_listening_is_not_a_barge_in() -> None:
    """An onset when the agent is NOT speaking is out of barge-in scope."""
    det = BargeInDetector()
    d = det.decide_barge_in(
        onset=_onset(),
        agent_state=AgentState.LISTENING,
        sustained_ms=500.0,
        ended=False,
    )
    assert d.verdict is BargeInVerdict.IGNORE
    assert d.reason == "not_persona_speaking"


def test_onset_while_thinking_is_not_a_barge_in() -> None:
    det = BargeInDetector()
    d = det.decide_barge_in(
        onset=_onset(),
        agent_state=AgentState.THINKING,
        sustained_ms=500.0,
        ended=False,
    )
    assert d.verdict is BargeInVerdict.IGNORE


# ---------- confidence/energy gate -----------------------------------------


def test_low_confidence_onset_is_ignored() -> None:
    """Below the confidence floor = noise / TTS bleed-through (D-V4-2)."""
    det = BargeInDetector(min_onset_confidence=0.6)
    d = det.decide_barge_in(
        onset=_onset(confidence=0.3),
        agent_state=AgentState.SPEAKING,
        sustained_ms=500.0,
        ended=False,
    )
    assert d.verdict is BargeInVerdict.IGNORE
    assert d.reason == "below_confidence_gate"


def test_provider_onset_without_confidence_bypasses_the_gate() -> None:
    """Provider/synthetic onsets carry no confidence → not gated on it."""
    det = BargeInDetector(min_onset_confidence=0.6, confirm_window_ms=200.0)
    d = det.decide_barge_in(
        onset=_onset(confidence=None, source="provider"),
        agent_state=AgentState.SPEAKING,
        sustained_ms=250.0,
        ended=False,
    )
    assert d.verdict is BargeInVerdict.INTERRUPT


# ---------- the responsive INTERRUPT path ----------------------------------


def test_sustained_onset_past_confirm_window_interrupts() -> None:
    det = BargeInDetector(confirm_window_ms=200.0)
    d = det.decide_barge_in(
        onset=_onset(),
        agent_state=AgentState.SPEAKING,
        sustained_ms=210.0,
        ended=False,
    )
    assert d.verdict is BargeInVerdict.INTERRUPT
    assert d.reason == "sustained_onset"


def test_onset_below_confirm_window_is_pending() -> None:
    """Not yet enough evidence — keep watching (don't interrupt, don't drop)."""
    det = BargeInDetector(confirm_window_ms=200.0)
    d = det.decide_barge_in(
        onset=_onset(),
        agent_state=AgentState.SPEAKING,
        sustained_ms=90.0,
        ended=False,
    )
    assert d.verdict is BargeInVerdict.PENDING
    assert d.reason == "awaiting_confirmation"


# ---------- the discriminating IGNORE path (backchannel bar) ----------------


def test_short_cough_that_ended_is_ignored() -> None:
    """A blip that ended below the backchannel bar = cough/click (D-V4-3)."""
    det = BargeInDetector(backchannel_bar_ms=350.0)
    d = det.decide_barge_in(
        onset=_onset(),
        agent_state=AgentState.SPEAKING,
        sustained_ms=80.0,
        ended=True,
    )
    assert d.verdict is BargeInVerdict.IGNORE
    assert d.reason == "backchannel_short_utterance"


def test_backchannel_mhm_that_ended_is_ignored() -> None:
    """A 250 ms "mhm" that already ended is a backchannel — keep talking."""
    det = BargeInDetector(backchannel_bar_ms=350.0)
    d = det.decide_barge_in(
        onset=_onset(),
        agent_state=AgentState.SPEAKING,
        sustained_ms=250.0,
        ended=True,
    )
    assert d.verdict is BargeInVerdict.IGNORE
    assert d.reason == "backchannel_short_utterance"


def test_completed_interruption_above_bar_interrupts() -> None:
    """A finished utterance longer than the backchannel bar is a real, if
    short, interruption."""
    det = BargeInDetector(backchannel_bar_ms=350.0)
    d = det.decide_barge_in(
        onset=_onset(),
        agent_state=AgentState.SPEAKING,
        sustained_ms=400.0,
        ended=True,
    )
    assert d.verdict is BargeInVerdict.INTERRUPT
    assert d.reason == "completed_interruption"


# ---------- threshold tunability -------------------------------------------


def test_thresholds_are_constructor_injected() -> None:
    """D-V4-X-rubric-and-params-tuning-surface — a hair-trigger detector."""
    snappy = BargeInDetector(confirm_window_ms=100.0)
    d = snappy.decide_barge_in(
        onset=_onset(),
        agent_state=AgentState.SPEAKING,
        sustained_ms=120.0,
        ended=False,
    )
    assert d.verdict is BargeInVerdict.INTERRUPT
