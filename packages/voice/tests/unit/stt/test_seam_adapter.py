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
