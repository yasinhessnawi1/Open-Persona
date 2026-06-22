"""Unit tests for ``persona_voice.stt.seam_adapter.V1STTStreamSeamAdapter``.

The seam adapter is the production composition root that wires V2's
:class:`StreamingSTT` + :class:`SileroVADAdapter` into a V1
:class:`STTStream`-Protocol-shaped object. These tests pin:

1. Structural conformance to V1's ``STTStream`` Protocol.
2. The audio-tee fan-out (push_audio reaches both backend + VAD).
3. Transcript passthrough (backend.transcripts → adapter.transcripts).
4. Activity-event merge per D-V2-2 + R-V2-2 combination_design:
   - Silero VAD events flow through with their original ``source="silero"``.
   - Provider speech_ended events are stamped ``corroborates=True``.
   - Listener notifications fire when listener is registered.
5. Lifecycle — load() forwards to VAD prewarm; close() cancels drainers +
   closes both sources idempotently.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from persona_voice.loop.streaming import STTStream, Transcript
from persona_voice.stt.config import StreamingSTTConfig
from persona_voice.stt.protocol import StreamGate
from persona_voice.stt.seam_adapter import V1STTStreamSeamAdapter
from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent
from persona_voice.stt.vad_silero import SileroVADAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---------- doubles ----------------------------------------------------------


class _FakeBackend:
    """Minimal StreamingSTT double — captures push_audio calls + scripts events."""

    def __init__(
        self,
        *,
        scripted_transcripts: list[Transcript] | None = None,
        scripted_activity_events: list[SpeechStartedEvent | SpeechEndedEvent] | None = None,
    ) -> None:
        self.pushed: list[tuple[bytes, int]] = []
        self.closed = False
        self._transcripts = scripted_transcripts or []
        self._activity = scripted_activity_events or []

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        self.pushed.append((pcm, sample_rate))

    async def transcripts(self) -> AsyncIterator[Transcript]:
        for t in self._transcripts:
            yield t

    async def speech_activity_events(
        self,
    ) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        for event in self._activity:
            yield event

    async def close(self) -> None:
        self.closed = True


class _FakeVAD:
    """Minimal SileroVADAdapter double — scripts activity-event stream."""

    def __init__(
        self,
        *,
        scripted_events: list[SpeechStartedEvent | SpeechEndedEvent] | None = None,
    ) -> None:
        self.pushed: list[tuple[bytes, int]] = []
        self.loaded = False
        self.closed = False
        self._events = scripted_events or []

    async def load(self) -> None:
        self.loaded = True

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        self.pushed.append((pcm, sample_rate))

    async def speech_activity_events(
        self,
    ) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        for event in self._events:
            yield event

    async def close(self) -> None:
        self.closed = True


class _CapturingListener:
    """Captures speech_started + speech_ended events for assertions."""

    def __init__(self) -> None:
        self.started: list[SpeechStartedEvent] = []
        self.ended: list[SpeechEndedEvent] = []

    async def on_speech_started(self, event: SpeechStartedEvent) -> None:
        self.started.append(event)

    async def on_speech_ended(self, event: SpeechEndedEvent) -> None:
        self.ended.append(event)


class _ToggleGate:
    """Minimal StreamGate double — ``is_open`` returns a mutable flag.

    Spec V8 D-V8-1. Lets a test flip the gate between frames to assert the
    split-tee: VAD always fed, backend fed only while open.
    """

    def __init__(self, *, open_: bool = True) -> None:
        self.open = open_
        self.calls = 0

    def is_open(self) -> bool:
        self.calls += 1
        return self.open


# ---------- helpers ----------------------------------------------------------


def _silero_started(ts: float = 0.5) -> SpeechStartedEvent:
    return SpeechStartedEvent(
        ts_audio_s=ts,
        ts_emit=datetime.now(UTC),
        source="silero",
        synthetic=False,
        confidence=0.92,
    )


def _silero_ended(ts: float = 2.0) -> SpeechEndedEvent:
    return SpeechEndedEvent(
        ts_audio_s=ts,
        ts_emit=datetime.now(UTC),
        source="silero",
        synthetic=False,
        transcript_settled=False,
        corroborates=False,
        silence_duration_ms=220,
    )


def _provider_ended(ts: float = 2.1) -> SpeechEndedEvent:
    return SpeechEndedEvent(
        ts_audio_s=ts,
        ts_emit=datetime.now(UTC),
        source="provider",
        synthetic=False,
        transcript_settled=True,
        corroborates=False,
        silence_duration_ms=None,
    )


def _make_real_vad() -> SileroVADAdapter:
    config = StreamingSTTConfig(
        provider="deepgram",
        api_key="test-key",  # type: ignore[arg-type]
        vad_library="silero",
    )
    return SileroVADAdapter(config)


# ---------- Protocol conformance ---------------------------------------------


def test_seam_adapter_satisfies_v1_stt_stream_protocol() -> None:
    """The adapter must structurally satisfy V1 STTStream (runtime_checkable)."""
    backend = _FakeBackend()
    vad = _make_real_vad()
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad)  # type: ignore[arg-type]
    assert isinstance(adapter, STTStream)


# ---------- push_audio tee ---------------------------------------------------


@pytest.mark.asyncio
async def test_push_audio_tees_to_backend_and_vad() -> None:
    backend = _FakeBackend()
    vad = _FakeVAD()
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad)  # type: ignore[arg-type]
    payload = b"\x01\x02" * 256
    await adapter.push_audio(payload, 16_000)
    assert backend.pushed == [(payload, 16_000)]
    assert vad.pushed == [(payload, 16_000)]
    await adapter.close()


@pytest.mark.asyncio
async def test_push_audio_after_close_is_noop() -> None:
    backend = _FakeBackend()
    vad = _FakeVAD()
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad)  # type: ignore[arg-type]
    await adapter.close()
    await adapter.push_audio(b"\x00\x00" * 256, 16_000)
    # Backend + VAD must NOT have received frames after close.
    assert backend.pushed == []
    assert vad.pushed == []


# ---------- Spec V8 D-V8-1: the split-tee cost gate -------------------------


def test_toggle_gate_satisfies_stream_gate_protocol() -> None:
    """The gate double must structurally satisfy StreamGate (runtime_checkable)."""
    assert isinstance(_ToggleGate(), StreamGate)


@pytest.mark.asyncio
async def test_absent_gate_streams_to_backend_pre_v8_behaviour() -> None:
    """No gate (None) ⇒ permanently open: backend receives every frame."""
    backend = _FakeBackend()
    vad = _FakeVAD()
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad)  # type: ignore[arg-type]
    assert adapter.gate is None
    payload = b"\x07\x08" * 256
    await adapter.push_audio(payload, 16_000)
    assert backend.pushed == [(payload, 16_000)]
    assert vad.pushed == [(payload, 16_000)]
    await adapter.close()


@pytest.mark.asyncio
async def test_open_gate_streams_to_both() -> None:
    backend = _FakeBackend()
    vad = _FakeVAD()
    gate = _ToggleGate(open_=True)
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad, gate=gate)  # type: ignore[arg-type]
    payload = b"\x09\x0a" * 256
    await adapter.push_audio(payload, 16_000)
    assert backend.pushed == [(payload, 16_000)]
    assert vad.pushed == [(payload, 16_000)]
    await adapter.close()


@pytest.mark.asyncio
async def test_closed_gate_withholds_backend_but_still_feeds_vad() -> None:
    """The load-bearing invariant: a closed gate never starves the VAD (barge-in)."""
    backend = _FakeBackend()
    vad = _FakeVAD()
    gate = _ToggleGate(open_=False)
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad, gate=gate)  # type: ignore[arg-type]
    payload = b"\x0b\x0c" * 256
    await adapter.push_audio(payload, 16_000)
    assert backend.pushed == []  # billed leg suppressed
    assert vad.pushed == [(payload, 16_000)]  # VAD always fed
    await adapter.close()


@pytest.mark.asyncio
async def test_gate_toggle_across_frames_only_open_frames_billed() -> None:
    backend = _FakeBackend()
    vad = _FakeVAD()
    gate = _ToggleGate(open_=True)
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad, gate=gate)  # type: ignore[arg-type]
    open_frame = b"\x01\x01" * 256
    closed_frame = b"\x02\x02" * 256
    reopened_frame = b"\x03\x03" * 256
    await adapter.push_audio(open_frame, 16_000)
    gate.open = False
    await adapter.push_audio(closed_frame, 16_000)
    gate.open = True
    await adapter.push_audio(reopened_frame, 16_000)
    # Backend saw only the two open frames; VAD saw all three.
    assert backend.pushed == [(open_frame, 16_000), (reopened_frame, 16_000)]
    assert vad.pushed == [
        (open_frame, 16_000),
        (closed_frame, 16_000),
        (reopened_frame, 16_000),
    ]
    await adapter.close()


@pytest.mark.asyncio
async def test_gate_setter_wires_after_construction() -> None:
    """The runner composition root sets the gate post-construction (D-V8-1)."""
    backend = _FakeBackend()
    vad = _FakeVAD()
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad)  # type: ignore[arg-type]
    gate = _ToggleGate(open_=False)
    adapter.gate = gate
    assert adapter.gate is gate
    await adapter.push_audio(b"\x04\x04" * 256, 16_000)
    assert backend.pushed == []
    assert vad.pushed == [(b"\x04\x04" * 256, 16_000)]
    await adapter.close()


@pytest.mark.asyncio
async def test_closed_adapter_ignores_gate_entirely() -> None:
    """After close(), push is a no-op regardless of gate state (close wins)."""
    backend = _FakeBackend()
    vad = _FakeVAD()
    gate = _ToggleGate(open_=True)
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad, gate=gate)  # type: ignore[arg-type]
    await adapter.close()
    await adapter.push_audio(b"\x05\x05" * 256, 16_000)
    assert backend.pushed == []
    assert vad.pushed == []


# ---------- Spec V8 D-V8-X-cost-rebase: the streamed-seconds instrument ------


@pytest.mark.asyncio
async def test_streamed_seconds_counts_only_billed_frames() -> None:
    """The instrument accumulates only audio actually forwarded to the backend."""
    backend = _FakeBackend()
    vad = _FakeVAD()
    gate = _ToggleGate(open_=True)
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad, gate=gate)  # type: ignore[arg-type]
    assert adapter.streamed_seconds == 0.0
    # One 256-sample frame = 512 bytes = 256 / 16000 s = 0.016 s.
    frame = b"\x01\x02" * 256
    await adapter.push_audio(frame, 16_000)
    assert adapter.streamed_seconds == pytest.approx(256 / 16_000)
    # Gate closed → frame withheld from backend → instrument does NOT advance.
    gate.open = False
    await adapter.push_audio(frame, 16_000)
    assert adapter.streamed_seconds == pytest.approx(256 / 16_000)
    # Reopen → counts again.
    gate.open = True
    await adapter.push_audio(frame, 16_000)
    assert adapter.streamed_seconds == pytest.approx(2 * 256 / 16_000)
    await adapter.close()


# ---------- Spec V8 D-V8-X-measure-stop-verdict: ring-buffer-on-reopen -------

# Each frame: 256 samples × 2 bytes = 512 B = 16 ms. reopen_preroll_ms=32 ⇒
# capacity = int(32/1000*16000)*2 = 1024 B = exactly 2 frames.
_C1 = b"\xa1\xa1" * 256
_C2 = b"\xa2\xa2" * 256
_C3 = b"\xa3\xa3" * 256
_O1 = b"\xb1\xb1" * 256
_O2 = b"\xb2\xb2" * 256


@pytest.mark.asyncio
async def test_no_ring_when_preroll_zero_reopen_sends_only_current() -> None:
    """reopen_preroll_ms=0 (default) ⇒ no flush — only the live open frame streams."""
    backend = _FakeBackend()
    vad = _FakeVAD()
    gate = _ToggleGate(open_=False)
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad, gate=gate)  # type: ignore[arg-type]
    await adapter.push_audio(_C1, 16_000)  # closed
    gate.open = True
    await adapter.push_audio(_O1, 16_000)  # reopen, but no ring
    assert backend.pushed == [(_O1, 16_000)]  # only the live frame, no pre-roll
    assert vad.pushed == [(_C1, 16_000), (_O1, 16_000)]  # VAD always fed
    await adapter.close()


@pytest.mark.asyncio
async def test_ring_flushes_capped_preroll_tail_on_reopen() -> None:
    """The gated-window tail (capped) is flushed ahead of the live frame on reopen."""
    backend = _FakeBackend()
    vad = _FakeVAD()
    gate = _ToggleGate(open_=False)
    adapter = V1STTStreamSeamAdapter(
        backend=backend,  # type: ignore[arg-type]
        vad=vad,  # type: ignore[arg-type]
        gate=gate,
        reopen_preroll_ms=32.0,  # capacity = 2 frames
    )
    # Three frames while closed → ring keeps the last two (C2, C3); none billed.
    for f in (_C1, _C2, _C3):
        await adapter.push_audio(f, 16_000)
    assert backend.pushed == []
    # Reopen → flush [C2, C3] then the live O1, IN ORDER.
    gate.open = True
    await adapter.push_audio(_O1, 16_000)
    assert backend.pushed == [(_C2, 16_000), (_C3, 16_000), (_O1, 16_000)]
    # Still open → only the live frame (ring was cleared; no re-send of C2/C3/O1).
    await adapter.push_audio(_O2, 16_000)
    assert backend.pushed == [
        (_C2, 16_000),
        (_C3, 16_000),
        (_O1, 16_000),
        (_O2, 16_000),
    ]
    # VAD saw every frame regardless of gate.
    assert vad.pushed == [
        (_C1, 16_000),
        (_C2, 16_000),
        (_C3, 16_000),
        (_O1, 16_000),
        (_O2, 16_000),
    ]
    # streamed_seconds counts exactly the 4 billed frames (C2,C3,O1,O2).
    assert adapter.streamed_seconds == pytest.approx(4 * 256 / 16_000)
    await adapter.close()


@pytest.mark.asyncio
async def test_ring_only_buffers_while_closed_no_double_send_across_reopens() -> None:
    """A second close→open flushes only the NEW gated tail — never re-sends earlier."""
    backend = _FakeBackend()
    vad = _FakeVAD()
    gate = _ToggleGate(open_=True)
    adapter = V1STTStreamSeamAdapter(
        backend=backend,  # type: ignore[arg-type]
        vad=vad,  # type: ignore[arg-type]
        gate=gate,
        reopen_preroll_ms=32.0,
    )
    await adapter.push_audio(_O1, 16_000)  # open: live only
    gate.open = False
    await adapter.push_audio(_C1, 16_000)  # closed: buffered
    gate.open = True
    await adapter.push_audio(_O2, 16_000)  # reopen: flush [C1] + O2
    assert backend.pushed == [(_O1, 16_000), (_C1, 16_000), (_O2, 16_000)]
    await adapter.close()


# ---------- transcripts passthrough -----------------------------------------


@pytest.mark.asyncio
async def test_transcripts_forward_from_backend() -> None:
    scripted = [
        Transcript(is_final=False, text="hel", confidence=0.7),
        Transcript(is_final=False, text="hello", confidence=0.9),
        Transcript(is_final=True, text="hello world", confidence=0.95),
    ]
    backend = _FakeBackend(scripted_transcripts=scripted)
    vad = _FakeVAD()
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad)  # type: ignore[arg-type]
    received: list[Transcript] = []
    async for transcript in adapter.transcripts():
        received.append(transcript)
    assert received == scripted
    await adapter.close()


# ---------- activity-event merge --------------------------------------------


@pytest.mark.asyncio
async def test_vad_speech_started_dispatched_to_listener() -> None:
    vad_event = _silero_started()
    backend = _FakeBackend()
    vad = _FakeVAD(scripted_events=[vad_event])
    listener = _CapturingListener()
    adapter = V1STTStreamSeamAdapter(
        backend=backend,  # type: ignore[arg-type]
        vad=vad,  # type: ignore[arg-type]
        listener=listener,
    )
    await adapter.push_audio(b"\x00\x00" * 256, 16_000)
    # Give drainer task a tick to run.
    await asyncio.sleep(0.05)
    assert len(listener.started) == 1
    assert listener.started[0].source == "silero"
    await adapter.close()


@pytest.mark.asyncio
async def test_provider_speech_ended_marked_corroborates_true() -> None:
    """D-V2-2 + R-V2-2 — provider speech_ended fires as corroborator."""
    provider_event = _provider_ended()
    backend = _FakeBackend(scripted_activity_events=[provider_event])
    vad = _FakeVAD()
    listener = _CapturingListener()
    adapter = V1STTStreamSeamAdapter(
        backend=backend,  # type: ignore[arg-type]
        vad=vad,  # type: ignore[arg-type]
        listener=listener,
    )
    await adapter.push_audio(b"\x00\x00" * 256, 16_000)
    await asyncio.sleep(0.05)
    assert len(listener.ended) == 1
    assert listener.ended[0].source == "provider"
    assert listener.ended[0].corroborates is True


@pytest.mark.asyncio
async def test_no_listener_means_events_silently_drained() -> None:
    backend = _FakeBackend(scripted_activity_events=[_provider_ended()])
    vad = _FakeVAD(scripted_events=[_silero_started(), _silero_ended()])
    adapter = V1STTStreamSeamAdapter(
        backend=backend,  # type: ignore[arg-type]
        vad=vad,  # type: ignore[arg-type]
        listener=None,
    )
    await adapter.push_audio(b"\x00\x00" * 256, 16_000)
    await asyncio.sleep(0.05)
    # No assertion error — drainers exit cleanly when listener is None.
    await adapter.close()


@pytest.mark.asyncio
async def test_listener_setter_wires_after_construction() -> None:
    backend = _FakeBackend()
    vad = _FakeVAD(scripted_events=[_silero_started()])
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad)  # type: ignore[arg-type]
    listener = _CapturingListener()
    adapter.listener = listener
    assert adapter.listener is listener
    await adapter.push_audio(b"\x00\x00" * 256, 16_000)
    await asyncio.sleep(0.05)
    assert len(listener.started) == 1
    await adapter.close()


# ---------- lifecycle -------------------------------------------------------


@pytest.mark.asyncio
async def test_load_forwards_to_vad_prewarm() -> None:
    backend = _FakeBackend()
    vad = _FakeVAD()
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad)  # type: ignore[arg-type]
    await adapter.load()
    assert vad.loaded is True


@pytest.mark.asyncio
async def test_close_is_idempotent_and_closes_both_sources() -> None:
    backend = _FakeBackend()
    vad = _FakeVAD()
    adapter = V1STTStreamSeamAdapter(backend=backend, vad=vad)  # type: ignore[arg-type]
    await adapter.close()
    assert backend.closed is True
    assert vad.closed is True
    # Second close — must not raise.
    await adapter.close()
