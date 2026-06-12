"""Unit tests for the V3 TTS Protocol surface — :class:`StreamingTTS` (T03).

Covers:

* ``@runtime_checkable`` ``isinstance`` semantics against a minimal mock
  implementation (Spec 02 ChatBackend mirror discipline — the Protocol is
  what callers depend on, not concrete backends).
* :meth:`StreamingTTS.synthesize` is ``def -> AsyncIterator`` (NOT
  ``async def``) — same shape as ``ChatBackend.chat_stream`` /
  ``StreamingSTT.transcripts`` per Spec 02 D-02-5.
* :class:`AudioChunk` re-exported from the ``persona_voice.tts`` package
  (V1's outbound record at ``loop/streaming.py:103``; V3's synthesis
  output reused verbatim).
* The capability + introspection property surface
  (``consumes_raw_text`` per D-V3-X-chunker-placement).
* A non-conforming object (missing ``cancel``) is rejected by ``isinstance``.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator

import pytest
from persona_voice.tts import AudioChunk, ResolvedVoice, StreamingTTS

_VOICE = ResolvedVoice(provider="mock-provider", voice_ref="voice-1")

# ---------- minimal mock implementation ------------------------------------


class _MockStreamingTTS:
    """Minimal :class:`StreamingTTS` impl for Protocol satisfaction tests."""

    def __init__(self) -> None:
        self.cancelled = False
        self.closed = False

    @property
    def provider_name(self) -> str:
        return "mock-provider"

    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def consumes_raw_text(self) -> bool:
        return False

    async def _iter(
        self, text_stream: AsyncIterator[str], voice: ResolvedVoice
    ) -> AsyncIterator[AudioChunk]:
        self.voice = voice
        async for _ in text_stream:
            yield AudioChunk(
                data=b"\x00\x00",
                sample_rate=24000,
                num_channels=1,
                samples_per_channel=1,
            )

    def synthesize(
        self, text_stream: AsyncIterator[str], voice: ResolvedVoice
    ) -> AsyncIterator[AudioChunk]:
        return self._iter(text_stream, voice)

    async def cancel(self) -> None:
        self.cancelled = True

    async def close(self) -> None:
        self.closed = True


class _MissingCancel:
    """Lacks ``cancel`` — must fail the runtime Protocol check."""

    @property
    def provider_name(self) -> str:
        return "x"

    @property
    def model_name(self) -> str:
        return "y"

    @property
    def consumes_raw_text(self) -> bool:
        return False

    def synthesize(
        self, text_stream: AsyncIterator[str], voice: ResolvedVoice
    ) -> AsyncIterator[AudioChunk]:
        raise NotImplementedError

    async def close(self) -> None:
        return None


# ---------- tests ----------------------------------------------------------


def test_mock_satisfies_runtime_checkable_protocol() -> None:
    assert isinstance(_MockStreamingTTS(), StreamingTTS)


def test_object_missing_cancel_fails_isinstance() -> None:
    assert not isinstance(_MissingCancel(), StreamingTTS)


def test_synthesize_is_sync_def_returning_async_iterator() -> None:
    # Spec 02 D-02-5: an async generator's type from the consumer's
    # perspective is AsyncIterator; the Protocol method is plain ``def``,
    # not ``async def`` — concrete impls are ``async def`` + ``yield``.
    assert not inspect.iscoroutinefunction(_MockStreamingTTS.synthesize)


def test_cancel_and_close_are_coroutine_functions() -> None:
    assert inspect.iscoroutinefunction(_MockStreamingTTS.cancel)
    assert inspect.iscoroutinefunction(_MockStreamingTTS.close)


@pytest.mark.asyncio
async def test_synthesize_yields_audio_chunks() -> None:
    async def _text() -> AsyncIterator[str]:
        yield "Hello."
        yield " World."

    backend = _MockStreamingTTS()
    chunks = [c async for c in backend.synthesize(_text(), _VOICE)]
    assert len(chunks) == 2
    assert all(isinstance(c, AudioChunk) for c in chunks)
    assert chunks[0].sample_rate == 24000


@pytest.mark.asyncio
async def test_cancel_and_close_set_state() -> None:
    backend = _MockStreamingTTS()
    await backend.cancel()
    await backend.close()
    assert backend.cancelled is True
    assert backend.closed is True


def test_consumes_raw_text_property_present() -> None:
    # D-V3-X-chunker-placement capability flag — launch backends return
    # False (client chunker load-bearing).
    assert _MockStreamingTTS().consumes_raw_text is False


def test_audio_chunk_reexported_from_tts_package() -> None:
    # V3 reuses V1's AudioChunk verbatim as the synthesis-output record.
    from persona_voice.loop.streaming import AudioChunk as LoopAudioChunk

    assert AudioChunk is LoopAudioChunk
