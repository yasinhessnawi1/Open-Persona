"""Unit tests for V2 STT Protocol surface — :class:`StreamingSTT` +
:class:`SpeechActivityListener`.

Covers:

* ``@runtime_checkable`` ``isinstance`` semantics against minimal mock
  implementations (Spec 02 ChatBackend mirror discipline — the Protocol
  is what callers depend on, not concrete backends).
* :class:`Transcript` re-exported from the ``persona_voice.stt`` package
  (boundary record V1 ships at ``loop/streaming.py:86-99``; V2 consumers
  import it from the ``stt`` package per D-V2-X-streaming-stt-vs-v1-sttstream-shape
  sibling lean).
* :meth:`StreamingSTT.transcripts` signature is ``def -> AsyncIterator``
  (not ``async def``) — same shape as ``ChatBackend.chat_stream``
  per Spec 02 D-02-5.
* :class:`SpeechActivityListener` Protocol shape — sensor-vs-decider
  boundary per D-V2-X-activity-listener-shape + Pipecat issue #1323
  production-bug precedent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from persona_voice.stt import (
    SpeechActivityListener,
    SpeechEndedEvent,
    SpeechStartedEvent,
    StreamingSTT,
    Transcript,
)


def _now() -> datetime:
    return datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)


# ---------- minimal mock implementations -----------------------------------


class _MockStreamingSTT:
    """Minimal :class:`StreamingSTT` impl for Protocol satisfaction tests."""

    def __init__(self) -> None:
        self.pushed: list[tuple[bytes, int]] = []
        self.closed = False

    @property
    def provider_name(self) -> str:
        return "mock-provider"

    @property
    def model_name(self) -> str:
        return "mock-model"

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        self.pushed.append((pcm, sample_rate))

    async def _iter(self) -> AsyncIterator[Transcript]:
        yield Transcript(is_final=False, text="hi", confidence=0.9)
        yield Transcript(is_final=True, text="hi there", confidence=0.95)

    def transcripts(self) -> AsyncIterator[Transcript]:
        return self._iter()

    async def _empty_activity(
        self,
    ) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        # Backends without provider-side activity surface return an empty
        # iterator per the StreamingSTT Protocol docstring (T06 seam adapter
        # drainer task exits cleanly).
        return
        yield  # pragma: no cover  # noqa: B901

    def speech_activity_events(
        self,
    ) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        return self._empty_activity()

    async def close(self) -> None:
        self.closed = True


class _MockSpeechActivityListener:
    """Minimal :class:`SpeechActivityListener` impl for Protocol tests."""

    def __init__(self) -> None:
        self.started: list[SpeechStartedEvent] = []
        self.ended: list[SpeechEndedEvent] = []

    async def on_speech_started(self, event: SpeechStartedEvent) -> None:
        self.started.append(event)

    async def on_speech_ended(self, event: SpeechEndedEvent) -> None:
        self.ended.append(event)


# ---------- StreamingSTT @runtime_checkable semantics ----------------------


def test_streaming_stt_is_runtime_checkable() -> None:
    impl = _MockStreamingSTT()
    assert isinstance(impl, StreamingSTT)


def test_streaming_stt_rejects_non_implementations() -> None:
    class _Missing:
        async def push_audio(self, pcm: bytes, sample_rate: int) -> None: ...

    assert not isinstance(_Missing(), StreamingSTT)


def test_streaming_stt_exposes_provider_and_model_properties() -> None:
    impl = _MockStreamingSTT()
    assert impl.provider_name == "mock-provider"
    assert impl.model_name == "mock-model"


@pytest.mark.asyncio
async def test_streaming_stt_push_audio_records_frames() -> None:
    impl = _MockStreamingSTT()
    await impl.push_audio(b"\x00\x00" * 320, 16000)
    await impl.push_audio(b"\x01\x00" * 320, 16000)
    assert impl.pushed == [(b"\x00\x00" * 320, 16000), (b"\x01\x00" * 320, 16000)]


@pytest.mark.asyncio
async def test_streaming_stt_transcripts_yields_partials_then_final() -> None:
    impl = _MockStreamingSTT()
    collected: list[Transcript] = []
    async for t in impl.transcripts():
        collected.append(t)
    assert len(collected) == 2
    assert collected[0].is_final is False
    assert collected[1].is_final is True
    assert collected[1].text == "hi there"


@pytest.mark.asyncio
async def test_streaming_stt_close_is_idempotent() -> None:
    impl = _MockStreamingSTT()
    await impl.close()
    await impl.close()
    assert impl.closed is True


# ---------- transcripts() shape is def -> AsyncIterator (Spec 02 D-02-5) ---


def test_streaming_stt_transcripts_signature_is_def_not_async_def() -> None:
    """Mirrors Spec 02 ``ChatBackend.chat_stream`` D-02-5 precedent.

    ``transcripts()`` must be a plain ``def`` that returns an
    :class:`AsyncIterator`, not an ``async def``. From the consumer's
    perspective, an async generator's type is :class:`AsyncIterator`
    (PEP 525); the Protocol declares the consumer view.
    """
    import inspect

    sig_member = StreamingSTT.transcripts
    assert not inspect.iscoroutinefunction(sig_member)


# ---------- SpeechActivityListener @runtime_checkable semantics ------------


def test_speech_activity_listener_is_runtime_checkable() -> None:
    listener = _MockSpeechActivityListener()
    assert isinstance(listener, SpeechActivityListener)


def test_speech_activity_listener_rejects_non_implementations() -> None:
    class _Missing:
        async def on_speech_started(self, event: SpeechStartedEvent) -> None: ...

    assert not isinstance(_Missing(), SpeechActivityListener)


@pytest.mark.asyncio
async def test_speech_activity_listener_records_started_and_ended() -> None:
    listener = _MockSpeechActivityListener()
    started = SpeechStartedEvent(ts_audio_s=1.0, ts_emit=_now(), source="silero")
    ended = SpeechEndedEvent(ts_audio_s=2.5, ts_emit=_now(), source="silero")
    await listener.on_speech_started(started)
    await listener.on_speech_ended(ended)
    assert listener.started == [started]
    assert listener.ended == [ended]


# ---------- Transcript re-export -------------------------------------------


def test_transcript_is_reexported_from_stt_package() -> None:
    """V2 consumers MUST be able to import :class:`Transcript` from
    ``persona_voice.stt`` directly per D-V2-X-streaming-stt-vs-v1-sttstream-shape
    sibling lean."""
    from persona_voice.loop.streaming import Transcript as V1Transcript

    assert Transcript is V1Transcript
