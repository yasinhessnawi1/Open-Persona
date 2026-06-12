"""Spec V3 integration spine (T13) — mocked-V5 text → V3 TTS → V1 outbound.

In-process integration (no live Cartesia / LiveKit Server — the backend's
real wire behaviour is the T14 external smoke). Proves:

* **Criterion #2 BINARY** — the first AudioChunk is emitted BEFORE the
  reply-text stream completes (streaming-everywhere; first audio before the
  model finishes generating).
* **Criterion #4/#5** — two personas with different ``voice`` selections
  resolve to different provider voices through the seam.
* **Criterion #7** — a mid-utterance ``cancel()`` produces near-immediate
  silence (no chunks after the barge-in; discard-on-cancel).
* **Criterion #10** — the combined V1 + V2 + V3 loop carries real synthesis
  end-to-end with echo disabled (scripted STT final → mocked-V5 tokens →
  V3 seam adapter → V1 outbound rail), and barge-in flushes the rail.
* **Criterion #9** — VoiceLog round-trips the additive TTS fields.

The barge-in bug classes from research.md §R-V3-5 (ghost audio / double-speak
/ late-frame leak / no-post-sentinel chunks) are exercised by the cancel +
interrupt paths.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from persona.schema.persona import CatalogueVoice
from persona_voice.logging import VoiceLog
from persona_voice.loop.streaming import (
    AudioChunk,
    PassThroughEchoMode,
    StreamingLoop,
    Transcript,
)
from persona_voice.session.state_machine import SessionStateMachine
from persona_voice.tts import ResolvedVoice, StreamingTTSConfig
from persona_voice.tts.seam_adapter import V1TTSStreamSeamAdapter, build_seam_adapter

_VOICE_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_VOICE_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_VOICE_TESTS_DIR))

from _mock_tts_backend import MockStreamingTTS  # type: ignore[import-not-found]  # noqa: E402

pytestmark = pytest.mark.integration


def _config() -> StreamingTTSConfig:
    return StreamingTTSConfig(provider="cartesia", api_key="k")  # type: ignore[arg-type]


def _chunk(n: int = 480) -> AudioChunk:
    return AudioChunk(
        data=b"\x00\x01" * n, sample_rate=24000, num_channels=1, samples_per_channel=n
    )


def _adapter(backend: MockStreamingTTS) -> V1TTSStreamSeamAdapter:
    return V1TTSStreamSeamAdapter(
        backend=backend,
        voice=ResolvedVoice(provider="mock", voice_ref="v-1"),
        config=_config(),
    )


class _TrackedText:
    """Reply-text stream that records consumption + completion for criterion 2."""

    def __init__(self, items: list[str]) -> None:
        self._items = items
        self.consumed = 0
        self.completed = False

    async def __aiter__(self) -> AsyncIterator[str]:
        for item in self._items:
            self.consumed += 1
            yield item
        self.completed = True


# ---------- Criterion #2 BINARY --------------------------------------------


@pytest.mark.asyncio
async def test_criterion_2_first_audio_before_text_completion() -> None:
    backend = MockStreamingTTS(chunks=[_chunk() for _ in range(6)])
    adapter = _adapter(backend)
    text = _TrackedText(
        [
            "The first sentence is clearly long enough, ",
            "with a trailing clause here. ",
            "A second full sentence follows along. ",
            "And a third and final sentence closes it.",
        ]
    )
    agen = adapter.synthesize(text)
    first = await agen.__anext__()
    # BINARY: audio is flowing while the reply text is still being generated.
    assert isinstance(first, AudioChunk)
    assert text.completed is False
    rest = [c async for c in agen]
    assert text.completed is True
    assert len(rest) >= 1


# ---------- Criterion #4/#5 — distinct voices ------------------------------


@pytest.mark.asyncio
async def test_two_personas_resolve_to_different_voices() -> None:
    astrid_backend = MockStreamingTTS()
    kai_backend = MockStreamingTTS()
    astrid = build_seam_adapter(
        backend=astrid_backend,
        config=_config(),
        voice_spec=CatalogueVoice(provider="mock", voice_id="astrid-voice"),
    )
    kai = build_seam_adapter(
        backend=kai_backend,
        config=_config(),
        voice_spec=CatalogueVoice(provider="mock", voice_id="kai-voice"),
    )
    _ = [c async for c in astrid.synthesize(_one_token("Hi."))]
    _ = [c async for c in kai.synthesize(_one_token("Hi."))]
    assert astrid_backend.received_voice is not None
    assert kai_backend.received_voice is not None
    assert astrid_backend.received_voice.voice_ref == "astrid-voice"
    assert kai_backend.received_voice.voice_ref == "kai-voice"
    assert astrid_backend.received_voice != kai_backend.received_voice


# ---------- Criterion #7 — cancel → silence --------------------------------


@pytest.mark.asyncio
async def test_cancel_mid_utterance_produces_silence() -> None:
    backend = MockStreamingTTS(chunks=[_chunk() for _ in range(10)])
    adapter = _adapter(backend)
    agen = adapter.synthesize(_multi("One. ", "Two. ", "Three. ", "Four. ", "Five and six here."))
    await agen.__anext__()  # first audio out
    await adapter.cancel()
    # No post-sentinel chunks (R-V3-5: no double-speak / ghost audio).
    remaining = [c async for c in agen]
    assert remaining == []
    assert backend.cancelled is True


# ---------- Criterion #10 — combined V1+V2+V3 loop -------------------------


class _ScriptedSTT:
    """Minimal V1 STTStream — yields one FINAL transcript then ends."""

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def provider_name(self) -> str:
        return "scripted"

    @property
    def model_name(self) -> str:
        return "scripted"

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:  # noqa: ARG002
        return None

    async def _iter(self) -> AsyncIterator[Transcript]:
        yield Transcript(is_final=True, text=self._text, confidence=0.95)

    def transcripts(self) -> AsyncIterator[Transcript]:
        return self._iter()

    async def close(self) -> None:
        return None


class _MockModel:
    """Mocked V5 ModelReplyProducer — yields scripted reply tokens."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    async def __call__(self, final_transcript: Transcript) -> AsyncIterator[str]:  # noqa: ARG002
        return self._yield()

    async def _yield(self) -> AsyncIterator[str]:
        for token in self._tokens:
            yield token


def _build_voice_room_fake() -> Any:  # noqa: ANN401
    vr = MagicMock()
    vr.set_inbound_handler = MagicMock()
    vr.set_disconnect_handler = MagicMock()
    vr.publish_outbound = AsyncMock()
    vr.capture_outbound_frame = AsyncMock()
    vr.clear_outbound = MagicMock()
    return vr


def _build_session() -> SessionStateMachine:
    engine = MagicMock()
    engine.dispose = MagicMock()
    return SessionStateMachine(
        session_id="s-v3",
        user_id="u-v3",
        persona_id="p-v3",
        conversation_id="c-v3",
        rls_engine=engine,
    )


@pytest.mark.asyncio
async def test_combined_v1_v2_v3_loop_carries_real_synthesis() -> None:
    vr = _build_voice_room_fake()
    session = _build_session()
    stt = _ScriptedSTT("hello there")
    model = _MockModel(["The reply, ", "with two sentences. ", "Here is the second."])
    tts = _adapter(MockStreamingTTS(chunks=[_chunk() for _ in range(8)]))
    loop = StreamingLoop(
        voice_room=vr,
        session=session,
        stt=stt,
        model=model,
        tts=tts,
        echo_mode=PassThroughEchoMode.DISABLED,
    )
    await loop.start_pipeline()
    await loop._pipeline_task  # type: ignore[arg-type]  # noqa: SLF001
    # V1+V2+V3 end-to-end: scripted final → V5 tokens → V3 audio → outbound.
    assert vr.capture_outbound_frame.await_count >= 1


@pytest.mark.asyncio
async def test_loop_interrupt_flushes_rail_and_cancels_tts() -> None:
    vr = _build_voice_room_fake()
    session = _build_session()
    backend = MockStreamingTTS(chunks=[_chunk() for _ in range(5)])
    tts = _adapter(backend)
    loop = StreamingLoop(voice_room=vr, session=session, tts=tts)
    await loop.interrupt()
    # Barge-in: TTS cancelled AND the outbound queue flushed (R-V3-5 ghost-audio).
    assert backend.cancelled is True
    vr.clear_outbound.assert_called_once()


# ---------- Criterion #9 — VoiceLog TTS fields round-trip -------------------


def test_voicelog_tts_fields_round_trip_end_to_end() -> None:
    ts = datetime.now(UTC)
    log = VoiceLog(
        session_id="s",
        user_id="u",
        persona_id="p",
        conversation_id="c",
        turn_index=0,
        started_at=ts,
        tts_text_first_at=ts,
        tts_first_audio_at=ts,
        tts_provider_cost_cents_per_minute=2.3,
        tts_total_cents=11.5,
    )
    rehydrated = VoiceLog.model_validate_json(log.model_dump_json())
    assert rehydrated.tts_first_audio_at == ts
    assert rehydrated.tts_total_cents == 11.5


# ---------- helpers --------------------------------------------------------


async def _one_token(text: str) -> AsyncIterator[str]:
    yield text


async def _multi(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item
