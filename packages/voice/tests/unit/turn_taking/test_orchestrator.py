"""Unit tests for the ConversationalOrchestrator (spec V4 T04).

Timing is fully deterministic: a :class:`_FakeScheduler` records scheduled
callbacks so the test fires them explicitly, and a :class:`_Clock` is advanced
by hand. No real ``asyncio.sleep`` and no ``datetime.now()`` in the decision
path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from persona_voice.loop.streaming import Transcript
from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent
from persona_voice.turn_taking.barge_in import BargeInDetector
from persona_voice.turn_taking.controller import TurnTakingController
from persona_voice.turn_taking.orchestrator import (
    ConversationalOrchestrator,
    SchedulerHandle,
)
from persona_voice.turn_taking.states import (
    AgentState,
    ConversationalState,
    ConversationalTransition,
    TransitionTrigger,
)

_BASE = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.asyncio


# ---------- test doubles ----------------------------------------------------


class _Clock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, ms: float) -> None:
        self._now = self._now + timedelta(milliseconds=ms)

    def set_ms_after_base(self, ms: float) -> None:
        self._now = _BASE + timedelta(milliseconds=ms)


class _FakeHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _FakeScheduler:
    """Records scheduled callbacks; the test fires them explicitly."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[float, Callable[[], Awaitable[None]], _FakeHandle]] = []

    def call_later(
        self, delay_s: float, callback: Callable[[], Awaitable[None]]
    ) -> SchedulerHandle:
        handle = _FakeHandle()
        self.scheduled.append((delay_s, callback, handle))
        return handle

    async def fire_last(self) -> None:
        """Fire the most recently scheduled (not-cancelled) callback."""
        for _delay, cb, handle in reversed(self.scheduled):
            if not handle.cancelled:
                await cb()
                return
        msg = "no live scheduled callback to fire"
        raise AssertionError(msg)


class _RecordingActions:
    def __init__(self) -> None:
        self.invoked: list[Transcript] = []
        self.cancelled = 0
        self.interrupted = 0

    async def invoke_model_for_turn(self, final_transcript: Transcript) -> None:
        self.invoked.append(final_transcript)

    async def cancel_generation(self) -> None:
        self.cancelled += 1

    async def interrupt(self) -> None:
        self.interrupted += 1


class _RecordingListener:
    def __init__(self) -> None:
        self.transitions: list[ConversationalTransition] = []

    async def on_state_changed(self, transition: ConversationalTransition) -> None:
        self.transitions.append(transition)


def _started(*, confidence: float | None = 0.9) -> SpeechStartedEvent:
    return SpeechStartedEvent(ts_audio_s=1.0, ts_emit=_BASE, source="silero", confidence=confidence)


def _ended(*, ts_emit: datetime | None = None, corroborates: bool = False) -> SpeechEndedEvent:
    return SpeechEndedEvent(
        ts_audio_s=2.0,
        ts_emit=ts_emit or _BASE,
        source="silero",
        corroborates=corroborates,
    )


def _build(
    clock: _Clock, scheduler: _FakeScheduler, actions: _RecordingActions, listener: object
) -> ConversationalOrchestrator:
    return ConversationalOrchestrator(
        actions=actions,  # type: ignore[arg-type]
        listener=listener,  # type: ignore[arg-type]
        controller=TurnTakingController(silence_threshold_ms=700.0),
        detector=BargeInDetector(confirm_window_ms=200.0, backchannel_bar_ms=350.0),
        scheduler=scheduler,
        clock=clock,
    )


# ---------- the canonical full-turn sequence --------------------------------


async def test_full_turn_sequence_listening_to_persona_speaking() -> None:
    """LISTENING → USER_SPEAKING → PROCESSING → PERSONA_SPEAKING → LISTENING."""
    clock = _Clock(_BASE)
    sched = _FakeScheduler()
    actions = _RecordingActions()
    listener = _RecordingListener()
    orch = _build(clock, sched, actions, listener)

    assert orch.state is ConversationalState.LISTENING

    # User starts speaking.
    await orch.on_speech_started(_started())
    assert orch.state is ConversationalState.USER_SPEAKING

    # A final transcript settles.
    await orch.on_transcript(Transcript(is_final=True, text="what time is it", confidence=0.95))

    # User stops; turn-end timer is armed.
    await orch.on_speech_ended(_ended(ts_emit=_BASE))
    # Advance the clock past the threshold, then fire the turn-end timer.
    clock.set_ms_after_base(800.0)
    await sched.fire_last()
    assert orch.state is ConversationalState.PROCESSING
    assert len(actions.invoked) == 1
    assert actions.invoked[0].text == "what time is it"

    # First audio reaches the rail.
    await orch.notify_model_first_audio()
    assert orch.state is ConversationalState.PERSONA_SPEAKING
    assert orch.agent_state is AgentState.SPEAKING
    assert orch.is_agent_speaking() is True

    # Persona finishes.
    await orch.notify_persona_finished()
    assert orch.state is ConversationalState.LISTENING

    # The four transitions were broadcast in order.
    triggers = [t.trigger for t in listener.transitions]
    assert triggers == [
        TransitionTrigger.USER_SPEECH_STARTED,
        TransitionTrigger.TURN_ENDED,
        TransitionTrigger.MODEL_FIRST_AUDIO,
        TransitionTrigger.PERSONA_FINISHED,
    ]


# ---------- mid-thought pause does not end the turn -------------------------


async def test_mid_thought_pause_does_not_end_turn() -> None:
    """User pauses then resumes — the turn-end timer is cancelled (criterion #2)."""
    clock = _Clock(_BASE)
    sched = _FakeScheduler()
    actions = _RecordingActions()
    orch = _build(clock, sched, actions, _RecordingListener())

    await orch.on_speech_started(_started())
    await orch.on_transcript(Transcript(is_final=False, text="I was thinking", confidence=0.5))
    await orch.on_speech_ended(_ended(ts_emit=_BASE))
    turn_end_handle = sched.scheduled[-1][2]

    # User resumes before the timer fires.
    await orch.on_speech_started(_started())
    assert turn_end_handle.cancelled is True
    assert orch.state is ConversationalState.USER_SPEAKING
    assert len(actions.invoked) == 0


# ---------- barge-in: sustained onset interrupts ----------------------------


async def _drive_to_persona_speaking(
    orch: ConversationalOrchestrator, sched: _FakeScheduler, clock: _Clock
) -> None:
    await orch.on_speech_started(_started())
    await orch.on_transcript(Transcript(is_final=True, text="tell me a story", confidence=0.95))
    await orch.on_speech_ended(_ended(ts_emit=_BASE))
    clock.set_ms_after_base(800.0)
    await sched.fire_last()
    await orch.notify_model_first_audio()
    assert orch.state is ConversationalState.PERSONA_SPEAKING


async def test_sustained_barge_in_interrupts_and_yields_floor() -> None:
    clock = _Clock(_BASE)
    sched = _FakeScheduler()
    actions = _RecordingActions()
    orch = _build(clock, sched, actions, _RecordingListener())
    await _drive_to_persona_speaking(orch, sched, clock)

    # User starts speaking over the persona; the onset is at "now".
    onset = SpeechStartedEvent(ts_audio_s=10.0, ts_emit=clock(), source="silero", confidence=0.9)
    await orch.on_speech_started(onset)
    # Still PERSONA_SPEAKING until the confirm window confirms.
    assert orch.state is ConversationalState.PERSONA_SPEAKING

    # Advance past the confirm window and fire the barge-in confirm timer.
    clock.advance(250.0)
    await sched.fire_last()

    assert actions.interrupted == 1
    assert orch.state is ConversationalState.USER_SPEAKING
    assert orch.is_agent_speaking() is False


# ---------- barge-in: short backchannel does NOT interrupt ------------------


async def test_backchannel_does_not_interrupt() -> None:
    """A short "mhm" that ends before the bar keeps the persona speaking (D-V4-3)."""
    clock = _Clock(_BASE)
    sched = _FakeScheduler()
    actions = _RecordingActions()
    orch = _build(clock, sched, actions, _RecordingListener())
    await _drive_to_persona_speaking(orch, sched, clock)

    onset = SpeechStartedEvent(ts_audio_s=10.0, ts_emit=clock(), source="silero", confidence=0.9)
    await orch.on_speech_started(onset)

    # The backchannel ends 250 ms later (below the 350 ms bar).
    clock.advance(250.0)
    await orch.on_speech_ended(_ended(ts_emit=clock()))

    assert actions.interrupted == 0
    assert orch.state is ConversationalState.PERSONA_SPEAKING
    # The pending confirm timer was cancelled when the offset resolved it.
    confirm_handle = sched.scheduled[-1][2]
    assert confirm_handle.cancelled is True


async def test_low_confidence_onset_does_not_interrupt() -> None:
    """Noise / TTS bleed-through (low confidence) never barges in (D-V4-2)."""
    clock = _Clock(_BASE)
    sched = _FakeScheduler()
    actions = _RecordingActions()
    orch = _build(clock, sched, actions, _RecordingListener())
    await _drive_to_persona_speaking(orch, sched, clock)

    onset = SpeechStartedEvent(ts_audio_s=10.0, ts_emit=clock(), source="silero", confidence=0.2)
    await orch.on_speech_started(onset)
    clock.advance(300.0)
    await sched.fire_last()

    assert actions.interrupted == 0
    assert orch.state is ConversationalState.PERSONA_SPEAKING


# ---------- continuation during PROCESSING (D-V4-5) ------------------------


async def test_user_continuation_during_processing_cancels_and_reopens() -> None:
    clock = _Clock(_BASE)
    sched = _FakeScheduler()
    actions = _RecordingActions()
    orch = _build(clock, sched, actions, _RecordingListener())

    await orch.on_speech_started(_started())
    await orch.on_transcript(Transcript(is_final=True, text="what is the time", confidence=0.9))
    await orch.on_speech_ended(_ended(ts_emit=_BASE))
    clock.set_ms_after_base(800.0)
    await sched.fire_last()
    assert orch.state is ConversationalState.PROCESSING

    # User adds more before the persona speaks.
    await orch.on_speech_started(_started())
    assert actions.cancelled == 1
    assert orch.state is ConversationalState.USER_SPEAKING


# ---------- mute-window provider --------------------------------------------


async def test_mute_window_only_open_while_persona_speaking() -> None:
    clock = _Clock(_BASE)
    sched = _FakeScheduler()
    orch = _build(clock, sched, _RecordingActions(), _RecordingListener())
    assert orch.is_agent_speaking() is False
    await _drive_to_persona_speaking(orch, sched, clock)
    assert orch.is_agent_speaking() is True


async def test_turn_end_records_endpoint_silence_wait() -> None:
    """T08 — the silence-wait at END_TURN is exposed for the dual-line latency."""
    clock = _Clock(_BASE)
    sched = _FakeScheduler()
    orch = _build(clock, sched, _RecordingActions(), _RecordingListener())
    assert orch.last_endpoint_silence_wait_ms is None

    await orch.on_speech_started(_started())
    await orch.on_transcript(Transcript(is_final=True, text="what time is it", confidence=0.95))
    await orch.on_speech_ended(_ended(ts_emit=_BASE))
    clock.set_ms_after_base(820.0)
    await sched.fire_last()
    # The controller measured ~820 ms of silence at the END_TURN decision.
    assert orch.last_endpoint_silence_wait_ms == pytest.approx(820.0)
