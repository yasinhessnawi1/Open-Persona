"""Unit tests for the V1 TTSStream seam adapter (T09).

Verifies the adapter satisfies V1's ``TTSStream`` Protocol, runs the
chunker in front of the backend (bypassing it when ``consumes_raw_text``),
passes the resolved voice through, and implements the iterator-sentinel
cancel (near-immediate silence + late-frame generation guard, D-V3-5 /
D-V3-X-cancel-flush-additive-shape). Uses the scripted ``MockStreamingTTS``.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from persona.schema.persona import CatalogueVoice
from persona_voice.loop.streaming import AudioChunk, TTSStream
from persona_voice.tts import ResolvedVoice, StreamingTTSConfig, TTSVoiceNotFoundError
from persona_voice.tts.seam_adapter import V1TTSStreamSeamAdapter, build_seam_adapter

# MockStreamingTTS lives in packages/voice/tests/_mock_tts_backend.py per
# Spec 02 D-02-12 (mock backends live in tests/, NOT in production src/). The
# root conftest only adds packages/*/src to sys.path, so extend with the voice
# tests dir for cross-test import (the V2 _mock_stt_backend pattern).
_VOICE_TESTS_DIR = Path(__file__).resolve().parents[2]
if str(_VOICE_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_VOICE_TESTS_DIR))

from _mock_tts_backend import MockStreamingTTS  # type: ignore[import-not-found]  # noqa: E402

_VOICE = ResolvedVoice(provider="mock", voice_ref="voice-1")


def _chunk(n: int = 480) -> AudioChunk:
    return AudioChunk(
        data=b"\x00\x01" * n, sample_rate=24000, num_channels=1, samples_per_channel=n
    )


def _config() -> StreamingTTSConfig:
    return StreamingTTSConfig(api_key="k")


def _adapter(backend: MockStreamingTTS) -> V1TTSStreamSeamAdapter:
    return V1TTSStreamSeamAdapter(backend=backend, voice=_VOICE, config=_config())


async def _text(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


# ---------- V1 Protocol conformance ----------------------------------------


def test_satisfies_v1_tts_stream_protocol() -> None:
    assert isinstance(_adapter(MockStreamingTTS()), TTSStream)


# ---------- chunk → backend → audio ----------------------------------------


@pytest.mark.asyncio
async def test_synthesize_chunks_then_yields_audio() -> None:
    backend = MockStreamingTTS(chunks=[_chunk(), _chunk(), _chunk()])
    adapter = _adapter(backend)
    # One long reply → the chunker produces multiple prosody-coherent chunks.
    frames = [
        f
        async for f in adapter.synthesize(
            _text("Hello there, friend. ", "This is a second full sentence here.")
        )
    ]
    assert all(isinstance(f, AudioChunk) for f in frames)
    # The backend saw chunked text (clause/sentence units), not raw tokens,
    # and the resolved voice was passed through.
    assert backend.received_voice == _VOICE
    assert backend.received_text  # the chunker emitted at least one chunk
    assert backend.received_text[0] == "Hello there,"


@pytest.mark.asyncio
async def test_consumes_raw_text_bypasses_chunker() -> None:
    backend = MockStreamingTTS(chunks=[_chunk()], consumes_raw_text=True)
    adapter = _adapter(backend)
    _ = [f async for f in adapter.synthesize(_text("raw token ", "stream"))]
    # Bypass: the backend received the raw token items verbatim, unchunked.
    assert backend.received_text == ["raw token ", "stream"]


# ---------- cancel ---------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_stops_stream_and_calls_backend() -> None:
    backend = MockStreamingTTS(chunks=[_chunk() for _ in range(5)])
    adapter = _adapter(backend)
    agen = adapter.synthesize(_text("One. ", "Two. ", "Three. ", "Four. ", "Five."))
    first = await agen.__anext__()
    assert isinstance(first, AudioChunk)
    await adapter.cancel()
    # Discard-on-cancel: no further frames after the barge-in.
    remaining = [f async for f in agen]
    assert remaining == []
    assert backend.cancelled is True


@pytest.mark.asyncio
async def test_cancel_is_idempotent() -> None:
    backend = MockStreamingTTS()
    adapter = _adapter(backend)
    await adapter.cancel()
    await adapter.cancel()  # no raise
    assert backend.cancelled is True


# ---------- build_seam_adapter ---------------------------------------------


def test_build_seam_adapter_resolves_voice() -> None:
    backend = MockStreamingTTS()  # provider_name == "mock"
    spec = CatalogueVoice(provider="mock", voice_id="v-42")
    adapter = build_seam_adapter(backend=backend, config=_config(), voice_spec=spec)
    assert isinstance(adapter, V1TTSStreamSeamAdapter)


def test_build_seam_adapter_uses_default_when_no_spec() -> None:
    backend = MockStreamingTTS()
    config = StreamingTTSConfig(api_key="k", voice_default="fallback-v")
    adapter = build_seam_adapter(backend=backend, config=config, voice_spec=None)
    assert isinstance(adapter, V1TTSStreamSeamAdapter)


def test_build_seam_adapter_raises_when_unresolvable() -> None:
    backend = MockStreamingTTS()
    # No spec and no default → TTSVoiceNotFoundError (D-V3-4).
    with pytest.raises(TTSVoiceNotFoundError):
        build_seam_adapter(backend=backend, config=_config(), voice_spec=None)
