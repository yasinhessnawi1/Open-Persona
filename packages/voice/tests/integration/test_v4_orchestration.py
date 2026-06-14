"""V4 orchestration integration test (spec V4 T06 — the wired-path proof).

Wires the real :class:`ConversationalOrchestrator` to a real
:class:`StreamingLoop` + :class:`SessionStateMachine` via
:func:`wire_orchestrated_loop`, with fakes only at the transport / model / TTS
boundary (no LiveKit, no Postgres). Drives a scripted timeline through the full
turn cycle and a barge-in, asserting:

* the four conversational states fire in order (criterion #5);
* the model is invoked via the loop with the user's transcript (criterion #1 wiring);
* on barge-in the rail is flushed (``clear_outbound``), the in-flight model
  generation is cancelled, and its unspoken remainder is never synthesised
  (criteria #3 + #8);
* the session's existing event seam receives the user-side lifecycle events.

Timing is deterministic: a fake scheduler + injected clock drive the
orchestrator's turn-end and barge-in timers; the cancellable stub model gates
itself after the first token so the barge-in lands mid-generation.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from persona_voice.loop.streaming import AudioChunk, StreamingLoop, Transcript
from persona_voice.session.state_machine import (
    SessionLifecycleEvent,
    SessionStateMachine,
)
from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent
from persona_voice.turn_taking.barge_in import BargeInDetector
from persona_voice.turn_taking.bridge import wire_orchestrated_loop
from persona_voice.turn_taking.controller import TurnTakingController
from persona_voice.turn_taking.heard_words import BargedReply
from persona_voice.turn_taking.orchestrator import SchedulerHandle
from persona_voice.turn_taking.states import (
    ConversationalState,
    ConversationalTransition,
    TransitionTrigger,
)

_VOICE_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_VOICE_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_VOICE_TESTS_DIR))

from _mock_model import CancellableStubModel  # type: ignore[import-not-found]  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_BASE = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


# ---------- deterministic timing doubles ------------------------------------


class _Clock:
    def __init__(self) -> None:
        self._now = _BASE

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
    def __init__(self) -> None:
        self.scheduled: list[tuple[Callable[[], Awaitable[None]], _FakeHandle]] = []

    def call_later(
        self, _delay_s: float, callback: Callable[[], Awaitable[None]]
    ) -> SchedulerHandle:
        handle = _FakeHandle()
        self.scheduled.append((callback, handle))
        return handle

    async def fire_last(self) -> None:
        for cb, handle in reversed(self.scheduled):
            if not handle.cancelled:
                await cb()
                return
        msg = "no live scheduled callback"
        raise AssertionError(msg)


# ---------- transport / TTS fakes -------------------------------------------


class _RecordingTTS:
    def __init__(self) -> None:
        self.received: list[str] = []
        self.cancel_calls = 0

    async def synthesize(self, text_stream: AsyncIterator[str]) -> AsyncIterator[AudioChunk]:
        async for tok in text_stream:
            self.received.append(tok)
            yield AudioChunk(
                data=b"\x00\x00", sample_rate=24_000, num_channels=1, samples_per_channel=1
            )

    async def cancel(self) -> None:
        self.cancel_calls += 1


def _voice_room_fake() -> Any:  # noqa: ANN401
    vr = MagicMock()
    vr.set_inbound_handler = MagicMock()
    vr.publish_outbound = _AsyncReturn(MagicMock())
    vr.capture_outbound_frame = _AsyncReturn(None)
    vr.clear_outbound = MagicMock(return_value=None)
    return vr


class _AsyncReturn:
    """A tiny awaitable-returning callable (AsyncMock alternative kept explicit)."""

    def __init__(self, value: Any) -> None:  # noqa: ANN401
        self._value = value
        self.calls = 0

    async def __call__(self, *_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
        self.calls += 1
        return self._value


class _V6Listener:
    def __init__(self) -> None:
        self.transitions: list[ConversationalTransition] = []

    async def on_state_changed(self, transition: ConversationalTransition) -> None:
        self.transitions.append(transition)


def _started(at: datetime, *, confidence: float = 0.9) -> SpeechStartedEvent:
    return SpeechStartedEvent(ts_audio_s=1.0, ts_emit=at, source="silero", confidence=confidence)


def _ended(at: datetime) -> SpeechEndedEvent:
    return SpeechEndedEvent(ts_audio_s=2.0, ts_emit=at, source="silero")


# ---------- the wired full turn cycle + barge-in ----------------------------


async def test_full_turn_cycle_then_barge_in_cancels_generation() -> None:
    clock = _Clock()
    sched = _FakeScheduler()
    vr = _voice_room_fake()
    session_events: list[SessionLifecycleEvent] = []

    async def _on_event(ev: SessionLifecycleEvent, _s: Any) -> None:  # noqa: ANN401
        session_events.append(ev)

    session = SessionStateMachine(
        session_id="s1",
        user_id="u1",
        persona_id="p1",
        conversation_id="c1",
        rls_engine=MagicMock(),
        on_event=_on_event,
    )
    model = CancellableStubModel(["Once ", "upon ", "a ", "time"], hold_after_first=True)
    tts = _RecordingTTS()
    loop = StreamingLoop(voice_room=vr, session=session, model=model, tts=tts)

    committed: list[BargedReply] = []

    class _V5Memory:
        async def on_reply_committed(self, reply: BargedReply) -> None:
            committed.append(reply)

    v6 = _V6Listener()
    orch = wire_orchestrated_loop(
        loop=loop,
        session=session,
        controller=TurnTakingController(silence_threshold_ms=700.0),
        detector=BargeInDetector(confirm_window_ms=200.0, backchannel_bar_ms=350.0),
        scheduler=sched,
        clock=clock,
        state_listener=v6,
        turn_transcript_listener=_V5Memory(),
    )

    # 1. User speaks.
    await orch.on_speech_started(_started(clock()))
    assert orch.state is ConversationalState.USER_SPEAKING
    # 2. Final transcript settles.
    await orch.on_transcript(Transcript(is_final=True, text="tell me a story", confidence=0.95))
    # 3. User stops → turn-end timer armed.
    await orch.on_speech_ended(_ended(clock()))
    # 4. Past the threshold, fire turn-end → PROCESSING + model invoked.
    clock.set_ms_after_base(800.0)
    await sched.fire_last()
    assert orch.state is ConversationalState.PROCESSING

    # 5. First audio reaches the rail → PERSONA_SPEAKING.
    await model.first_token.wait()
    assert model.calls == ["tell me a story"]
    assert orch.state is ConversationalState.PERSONA_SPEAKING
    assert orch.is_agent_speaking() is True
    assert tts.received == ["Once "]

    # 6. User barges in (onset at the current instant).
    await orch.on_speech_started(_started(clock()))
    assert orch.state is ConversationalState.PERSONA_SPEAKING  # not yet confirmed
    # 7. Past the confirm window, fire the barge-in confirm → INTERRUPT.
    clock.advance(250.0)
    await sched.fire_last()

    assert orch.state is ConversationalState.USER_SPEAKING
    assert orch.is_agent_speaking() is False
    # The rail was flushed and TTS cancelled (barge-in stop-and-flush).
    vr.clear_outbound.assert_called_once()
    assert tts.cancel_calls == 1
    # The in-flight generation was cancelled; its unspoken remainder was never
    # synthesised (memory honesty / criterion #8).
    assert model.cancelled is True
    assert model.emitted == ["Once "]
    assert tts.received == ["Once "]
    # Memory honesty (criterion #8 / D-V4-4): V5 receives a truncated reply
    # carrying only the heard prefix — the unspoken remainder never recorded.
    assert committed == [BargedReply(heard_text="Once ", truncated=True, token_count=1)]

    # 8. The four conversational states fired in order (criterion #5).
    assert [t.trigger for t in v6.transitions] == [
        TransitionTrigger.USER_SPEECH_STARTED,
        TransitionTrigger.TURN_ENDED,
        TransitionTrigger.MODEL_FIRST_AUDIO,
        TransitionTrigger.BARGE_IN,
    ]

    # 9. The session's existing seam received the user-side lifecycle events
    #    plus the agent events the loop emits (no transition-logic change).
    assert SessionLifecycleEvent.USER_STARTED_SPEAKING in session_events
    assert SessionLifecycleEvent.USER_STOPPED_SPEAKING in session_events
    assert SessionLifecycleEvent.AGENT_STARTED_SPEAKING in session_events
    assert SessionLifecycleEvent.AGENT_STOPPED_SPEAKING in session_events
    # Exactly one AGENT_STOPPED on barge-in (no duplicate from the teardown half).
    assert session_events.count(SessionLifecycleEvent.AGENT_STOPPED_SPEAKING) == 1


async def test_clean_turn_returns_floor_to_listening() -> None:
    """A reply that finishes (not barged) returns the floor to LISTENING."""
    clock = _Clock()
    sched = _FakeScheduler()
    vr = _voice_room_fake()
    session = SessionStateMachine(
        session_id="s",
        user_id="u",
        persona_id="p",
        conversation_id="c",
        rls_engine=MagicMock(),
    )
    model = CancellableStubModel(["Hello ", "there"], hold_after_first=False)
    tts = _RecordingTTS()
    loop = StreamingLoop(voice_room=vr, session=session, model=model, tts=tts)
    v6 = _V6Listener()
    orch = wire_orchestrated_loop(
        loop=loop,
        session=session,
        scheduler=sched,
        clock=clock,
        state_listener=v6,
    )

    await orch.on_speech_started(_started(clock()))
    await orch.on_transcript(Transcript(is_final=True, text="hi", confidence=0.95))
    await orch.on_speech_ended(_ended(clock()))
    clock.set_ms_after_base(800.0)
    await sched.fire_last()
    # Let the (ungated) generation stream all tokens and finish.
    await model.first_token.wait()
    # Drain the spawned model task to completion.
    inflight = orch._actions._task  # type: ignore[attr-defined]  # noqa: SLF001
    assert inflight is not None
    await inflight

    assert tts.received == ["Hello ", "there"]
    assert orch.state is ConversationalState.LISTENING
    assert [t.trigger for t in v6.transitions][-1] is TransitionTrigger.PERSONA_FINISHED
