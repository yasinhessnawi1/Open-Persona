"""Unit tests for the graceful-degradation policy (spec V4 T09 — D-V4-6).

The lean-conservative bias: under ambiguity, err toward the less-disruptive
failure — a slightly-long pause over interrupting the user; ignoring a possible
non-turn over responding to noise; never leaving the call wedged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent
from persona_voice.turn_taking.barge_in import BargeInDetector
from persona_voice.turn_taking.controller import TurnTakingController, TurnVerdict
from persona_voice.turn_taking.orchestrator import ConversationalOrchestrator
from persona_voice.turn_taking.states import ConversationalState

_BASE = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


def _offset(*, corroborates: bool = False) -> SpeechEndedEvent:
    return SpeechEndedEvent(
        ts_audio_s=2.0, ts_emit=_BASE, source="silero", corroborates=corroborates
    )


def _at(ms: float) -> datetime:
    return _BASE + timedelta(milliseconds=ms)


# ---------- conservative endpointing (controller) ---------------------------


def test_ambiguous_endpoint_no_transcript_waits() -> None:
    """Silence past threshold but uncorroborated AND no transcript → WAIT.

    Erring toward not responding to a likely noise/false-VAD blip (D-V4-6)."""
    ctrl = TurnTakingController(silence_threshold_ms=700.0)
    d = ctrl.decide_turn_end(last_offset=_offset(), settled_text=None, now=_at(900.0))
    assert d.verdict is TurnVerdict.WAIT
    assert d.reason == "ambiguous_no_transcript"


def test_corroborated_endpoint_with_thin_text_is_trusted() -> None:
    """A provider-corroborated offset is trusted even with thin/empty text."""
    ctrl = TurnTakingController(corroborated_silence_threshold_ms=300.0)
    d = ctrl.decide_turn_end(
        last_offset=_offset(corroborates=True), settled_text="", now=_at(400.0)
    )
    assert d.verdict is TurnVerdict.END_TURN


def test_uncorroborated_waits_longer_than_corroborated() -> None:
    """Absent corroboration leans conservative — a longer wait than corroborated."""
    ctrl = TurnTakingController(silence_threshold_ms=700.0, corroborated_silence_threshold_ms=300.0)
    # At 400 ms: corroborated would END, uncorroborated still WAITs.
    corr = ctrl.decide_turn_end(
        last_offset=_offset(corroborates=True), settled_text="done", now=_at(400.0)
    )
    uncorr = ctrl.decide_turn_end(last_offset=_offset(), settled_text="done", now=_at(400.0))
    assert corr.verdict is TurnVerdict.END_TURN
    assert uncorr.verdict is TurnVerdict.WAIT


def test_degradation_can_be_disabled() -> None:
    """The bias is a tuning surface — disabling it restores pure-silence ending."""
    ctrl = TurnTakingController(silence_threshold_ms=700.0, conservative_degradation=False)
    d = ctrl.decide_turn_end(last_offset=_offset(), settled_text=None, now=_at(900.0))
    assert d.verdict is TurnVerdict.END_TURN


# ---------- conservative barge-in: jitter (orchestrator) ---------------------


class _Clock:
    def __init__(self) -> None:
        self._now = _BASE

    def __call__(self) -> datetime:
        return self._now

    def advance(self, ms: float) -> None:
        self._now = self._now + timedelta(milliseconds=ms)

    def set_ms(self, ms: float) -> None:
        self._now = _BASE + timedelta(milliseconds=ms)


class _FakeHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _FakeScheduler:
    def __init__(self) -> None:
        self.scheduled: list[tuple[object, _FakeHandle]] = []

    def call_later(self, _delay_s: float, callback: object) -> _FakeHandle:
        handle = _FakeHandle()
        self.scheduled.append((callback, handle))
        return handle

    async def fire_last(self) -> None:
        for cb, handle in reversed(self.scheduled):
            if not handle.cancelled:
                await cb()  # type: ignore[operator]
                return


class _Actions:
    def __init__(self) -> None:
        self.interrupted = 0
        self.cancelled = 0

    async def invoke_model_for_turn(self, _t: object) -> None: ...
    async def cancel_generation(self) -> None:
        self.cancelled += 1

    async def interrupt(self) -> None:
        self.interrupted += 1


async def _to_persona_speaking(
    orch: ConversationalOrchestrator, sched: _FakeScheduler, clock: _Clock
) -> None:
    from persona_voice.loop.streaming import Transcript

    await orch.on_speech_started(
        SpeechStartedEvent(ts_audio_s=1.0, ts_emit=clock(), source="silero", confidence=0.9)
    )
    await orch.on_transcript(Transcript(is_final=True, text="tell me a story", confidence=0.9))
    await orch.on_speech_ended(SpeechEndedEvent(ts_audio_s=2.0, ts_emit=clock(), source="silero"))
    clock.set_ms(800.0)
    await sched.fire_last()
    await orch.notify_model_first_audio()


@pytest.mark.asyncio
async def test_jittery_onset_blips_do_not_barge_in() -> None:
    """Three quick on/off blips during persona speech → no interruption (D-V4-6)."""
    clock = _Clock()
    sched = _FakeScheduler()
    actions = _Actions()
    orch = ConversationalOrchestrator(
        actions=actions,  # type: ignore[arg-type]
        detector=BargeInDetector(confirm_window_ms=200.0, backchannel_bar_ms=350.0),
        scheduler=sched,
        clock=clock,
    )
    await _to_persona_speaking(orch, sched, clock)

    for _ in range(3):
        onset = SpeechStartedEvent(
            ts_audio_s=10.0, ts_emit=clock(), source="silero", confidence=0.9
        )
        await orch.on_speech_started(onset)
        clock.advance(80.0)  # a short blip, below the confirm window
        await orch.on_speech_ended(
            SpeechEndedEvent(ts_audio_s=10.5, ts_emit=clock(), source="silero")
        )
        clock.advance(20.0)

    assert actions.interrupted == 0
    assert orch.state is ConversationalState.PERSONA_SPEAKING


@pytest.mark.asyncio
async def test_force_reset_returns_floor_to_listening() -> None:
    """The watchdog/degradation recovery — force the machine back to LISTENING."""
    clock = _Clock()
    sched = _FakeScheduler()
    orch = ConversationalOrchestrator(
        actions=_Actions(),  # type: ignore[arg-type]
        scheduler=sched,
        clock=clock,
    )
    await _to_persona_speaking(orch, sched, clock)
    assert orch.state is ConversationalState.PERSONA_SPEAKING
    await orch.force_reset()
    assert orch.state is ConversationalState.LISTENING
    # Idempotent — force_reset from LISTENING is a no-op.
    await orch.force_reset()
    assert orch.state is ConversationalState.LISTENING
