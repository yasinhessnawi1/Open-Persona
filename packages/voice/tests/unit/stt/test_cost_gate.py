"""Unit tests for Spec V8 cost gates (``persona_voice.stt.cost_gate``).

Two layers:

1. **Pure gate logic** — :class:`PersonaSpeakingGate` opens iff the persona is
   not speaking; ``source=None`` is permanently open; Protocol conformance.
2. **The closed-loop barge-in-resume proof (D-V8-2 acceptance #3).** A *real*
   :class:`ConversationalOrchestrator` driven to ``PERSONA_SPEAKING`` →  gate
   closed (billed backend unbilled) → the user barges in → the Silero onset
   STILL fires (the VAD is fed by the split-tee even while gated) → it drains
   through the real seam adapter to the orchestrator → the orchestrator leaves
   ``PERSONA_SPEAKING`` → the gate REOPENS → the backend hears the user. This
   proves #1 saves cost without breaking interruption.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from persona_voice.loop.streaming import Transcript
from persona_voice.stt.cost_gate import (
    AgentSpeakingSource,
    IdleAwareGate,
    PersonaSpeakingGate,
    UserTurnSource,
)
from persona_voice.stt.protocol import StreamGate
from persona_voice.stt.seam_adapter import V1STTStreamSeamAdapter
from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent
from persona_voice.turn_taking.orchestrator import ConversationalOrchestrator, SchedulerHandle
from persona_voice.turn_taking.states import ConversationalState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_BASE = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


# ---------- doubles ---------------------------------------------------------


class _FakeSpeaking:
    """Minimal AgentSpeakingSource double with a mutable speaking flag."""

    def __init__(self, *, speaking: bool = False) -> None:
        self.speaking = speaking

    def is_agent_speaking(self) -> bool:
        return self.speaking


class _FakeUserTurn:
    """Minimal UserTurnSource double with a mutable active flag."""

    def __init__(self, *, active: bool = False) -> None:
        self.active = active

    def is_user_turn_active(self) -> bool:
        return self.active


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


class _FakeBackend:
    """StreamingSTT double — records billed push_audio + empty activity stream."""

    def __init__(self) -> None:
        self.pushed: list[tuple[bytes, int]] = []
        self.closed = False

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        self.pushed.append((pcm, sample_rate))

    async def transcripts(self) -> AsyncIterator[Transcript]:
        return
        yield  # pragma: no cover — empty async generator

    async def speech_activity_events(self) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        return
        yield  # pragma: no cover — no provider activity in this test

    async def close(self) -> None:
        self.closed = True


class _QueueVAD:
    """SileroVADAdapter double — records every frame; emits activity on demand.

    ``speech_activity_events`` drains an internal queue (mirroring the real
    adapter), so a test can ``emit`` an onset exactly when it wants the
    barge-in to fire — proving the VAD is still fed while the gate is closed.
    """

    def __init__(self) -> None:
        self.pushed: list[tuple[bytes, int]] = []
        self.loaded = False
        self.closed = False
        self._q: asyncio.Queue[SpeechStartedEvent | SpeechEndedEvent | None] = asyncio.Queue()

    async def load(self) -> None:
        self.loaded = True

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        self.pushed.append((pcm, sample_rate))

    def emit(self, event: SpeechStartedEvent | SpeechEndedEvent) -> None:
        self._q.put_nowait(event)

    async def speech_activity_events(self) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        while True:
            event = await self._q.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self.closed = True
        self._q.put_nowait(None)


def _started(ts_emit: datetime, *, confidence: float = 0.9) -> SpeechStartedEvent:
    return SpeechStartedEvent(
        ts_audio_s=1.0, ts_emit=ts_emit, source="silero", confidence=confidence
    )


def _ended(ts_emit: datetime) -> SpeechEndedEvent:
    return SpeechEndedEvent(ts_audio_s=2.0, ts_emit=ts_emit, source="silero")


# ---------- pure gate logic -------------------------------------------------


def test_persona_speaking_gate_satisfies_stream_gate_protocol() -> None:
    assert isinstance(PersonaSpeakingGate(), StreamGate)


def test_fake_source_satisfies_agent_speaking_source_protocol() -> None:
    assert isinstance(_FakeSpeaking(), AgentSpeakingSource)


def test_real_orchestrator_satisfies_agent_speaking_source_protocol() -> None:
    orch = ConversationalOrchestrator(actions=_RecordingActions())
    assert isinstance(orch, AgentSpeakingSource)


def test_gate_with_no_source_is_permanently_open() -> None:
    """source=None ⇒ pre-V8 behaviour (always stream)."""
    gate = PersonaSpeakingGate()
    assert gate.source is None
    assert gate.is_open() is True


def test_gate_closed_while_persona_speaking() -> None:
    gate = PersonaSpeakingGate(source=_FakeSpeaking(speaking=True))
    assert gate.is_open() is False


def test_gate_open_while_persona_not_speaking() -> None:
    gate = PersonaSpeakingGate(source=_FakeSpeaking(speaking=False))
    assert gate.is_open() is True


def test_gate_tracks_source_state_live() -> None:
    source = _FakeSpeaking(speaking=False)
    gate = PersonaSpeakingGate(source=source)
    assert gate.is_open() is True
    source.speaking = True
    assert gate.is_open() is False
    source.speaking = False
    assert gate.is_open() is True


def test_source_setter_wires_after_construction() -> None:
    gate = PersonaSpeakingGate()
    source = _FakeSpeaking(speaking=True)
    gate.source = source
    assert gate.source is source
    assert gate.is_open() is False


# ---------- idle-gate (the shipped production gate) --------------------------


def test_idle_aware_gate_satisfies_stream_gate_protocol() -> None:
    assert isinstance(IdleAwareGate(), StreamGate)


def test_fake_user_turn_satisfies_user_turn_source_protocol() -> None:
    assert isinstance(_FakeUserTurn(), UserTurnSource)


def test_real_orchestrator_satisfies_user_turn_source_protocol() -> None:
    orch = ConversationalOrchestrator(actions=_RecordingActions())
    assert isinstance(orch, UserTurnSource)


def test_idle_gate_with_no_source_is_permanently_open() -> None:
    gate = IdleAwareGate()
    assert gate.source is None
    assert gate.is_open() is True


def test_idle_gate_open_only_during_user_turn() -> None:
    source = _FakeUserTurn(active=False)
    gate = IdleAwareGate(source=source)
    assert gate.is_open() is False  # persona/idle/preparing → closed
    source.active = True
    assert gate.is_open() is True  # user turn active → open


@pytest.mark.asyncio
async def test_orchestrator_is_user_turn_active_projection() -> None:
    """is_user_turn_active is True iff USER_SPEAKING or PROCESSING."""
    orch = ConversationalOrchestrator(actions=_RecordingActions())
    assert orch.state is ConversationalState.LISTENING
    assert orch.is_user_turn_active() is False  # LISTENING idle
    # Drive LISTENING → USER_SPEAKING via a user onset.
    await orch.on_speech_started(_started(_BASE))
    assert orch.state is ConversationalState.USER_SPEAKING
    assert orch.is_user_turn_active() is True


# ---------- the closed-loop barge-in-resume proof (D-V8-2, acceptance #3) ----


async def _drive_to_persona_speaking(
    orch: ConversationalOrchestrator, sched: _FakeScheduler, clock: _Clock
) -> None:
    """Walk a real orchestrator LISTENING → … → PERSONA_SPEAKING (real path)."""
    await orch.on_speech_started(_started(_BASE))
    await orch.on_transcript(Transcript(is_final=True, text="tell me a story", confidence=0.95))
    await orch.on_speech_ended(_ended(_BASE))
    clock.set_ms_after_base(800.0)
    await sched.fire_last()  # turn-end timer → PROCESSING + invoke_model
    await orch.notify_model_first_audio()  # PROCESSING → PERSONA_SPEAKING
    assert orch.state is ConversationalState.PERSONA_SPEAKING


@pytest.mark.asyncio
async def test_barge_in_resume_closed_loop() -> None:
    clock = _Clock(_BASE)
    sched = _FakeScheduler()
    actions = _RecordingActions()
    orch = ConversationalOrchestrator(actions=actions, scheduler=sched, clock=clock)
    await _drive_to_persona_speaking(orch, sched, clock)

    gate = PersonaSpeakingGate(source=orch)
    backend = _FakeBackend()
    vad = _QueueVAD()
    adapter = V1STTStreamSeamAdapter(
        backend=backend,  # type: ignore[arg-type]
        vad=vad,  # type: ignore[arg-type]
        gate=gate,
        listener=orch,
    )

    # 1) Persona is speaking → gate CLOSED → billed backend gets nothing, but
    #    the VAD is STILL fed (the split-tee invariant that keeps barge-in alive).
    assert orch.is_agent_speaking() is True
    assert gate.is_open() is False
    gated_frame = b"\x10\x11" * 256
    await adapter.push_audio(gated_frame, 16_000)
    assert backend.pushed == []
    assert vad.pushed == [(gated_frame, 16_000)]

    # 2) The user barges in: the VAD (still fed) emits a confident onset, which
    #    drains through the real seam adapter to the orchestrator.
    onset = _started(clock(), confidence=0.9)
    vad.emit(onset)
    await asyncio.sleep(0.05)  # let the seam-adapter VAD drainer dispatch
    # Still PERSONA_SPEAKING until the confirm window confirms the barge-in.
    assert orch.state is ConversationalState.PERSONA_SPEAKING

    # 3) Confirm the barge-in: advance past the confirm window + fire the timer →
    #    the orchestrator interrupts the persona and leaves PERSONA_SPEAKING.
    clock.advance(250.0)
    await sched.fire_last()
    assert actions.interrupted == 1
    assert orch.state is ConversationalState.USER_SPEAKING
    assert orch.is_agent_speaking() is False

    # 4) Gate REOPENS → the backend now hears the user's audio (criterion #3).
    assert gate.is_open() is True
    user_frame = b"\x20\x21" * 256
    await adapter.push_audio(user_frame, 16_000)
    assert backend.pushed == [(user_frame, 16_000)]
    assert vad.pushed == [(gated_frame, 16_000), (user_frame, 16_000)]

    await adapter.close()


@pytest.mark.asyncio
async def test_idle_gate_resume_preserves_first_word_via_ring() -> None:
    """Idle-gate closed-loop: LISTENING idle → gate closed → user onset → reopen
    flushes the buffered pre-roll so the first post-idle word is NOT clipped."""
    orch = ConversationalOrchestrator(actions=_RecordingActions())
    assert orch.state is ConversationalState.LISTENING
    assert orch.is_user_turn_active() is False

    gate = IdleAwareGate(source=orch)
    backend = _FakeBackend()
    vad = _QueueVAD()
    adapter = V1STTStreamSeamAdapter(
        backend=backend,  # type: ignore[arg-type]
        vad=vad,  # type: ignore[arg-type]
        gate=gate,
        listener=orch,
        reopen_preroll_ms=32.0,  # 2-frame ring
    )

    # 1) LISTENING idle → gate closed → backend gets nothing; the ring buffers the
    #    last 2 frames; the VAD is still fed (so onset can fire).
    idle1 = b"\xc1\xc1" * 256
    idle2 = b"\xc2\xc2" * 256
    await adapter.push_audio(idle1, 16_000)
    await adapter.push_audio(idle2, 16_000)
    assert backend.pushed == []
    assert vad.pushed == [(idle1, 16_000), (idle2, 16_000)]

    # 2) The user starts speaking: the VAD (fed) emits an onset → LISTENING →
    #    USER_SPEAKING (immediate; no confirm window from LISTENING).
    vad.emit(_started(_BASE, confidence=0.9))
    await asyncio.sleep(0.05)
    assert orch.state is ConversationalState.USER_SPEAKING
    assert orch.is_user_turn_active() is True

    # 3) Next user frame → reopen → flush the pre-roll tail [idle1, idle2] ahead of
    #    the live frame, so the provider hears the run-up to the first word.
    live = b"\xd1\xd1" * 256
    await adapter.push_audio(live, 16_000)
    assert backend.pushed == [(idle1, 16_000), (idle2, 16_000), (live, 16_000)]

    await adapter.close()
