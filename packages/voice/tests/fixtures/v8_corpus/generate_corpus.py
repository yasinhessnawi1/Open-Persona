"""Spec V8 corpus provenance — render V8_CORPUS via real Cartesia → 16 kHz PCM16 .raw.

On-demand generator (D-V8-10), NOT a test. Run once to (re)produce the fixtures the
committed live replay (``tests/external/test_v8_cost_gating_live.py``) consumes:

    SSL_CERT_FILE=$(python -m certifi) \
    PERSONA_TTS_API_KEY=... PERSONA_TTS_VOICE_DEFAULT=... \
    uv run python packages/voice/tests/fixtures/v8_corpus/generate_corpus.py

Cartesia emits raw pcm_s16le @ 24 kHz (the V1 outbound rail); we resample to the
16 kHz Deepgram inbound rate (D-V1-6). The 5 utterances + their failure-mode roles
are the single source of truth in ``persona_voice.stt.cost_harness.V8_CORPUS``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

_OUT = Path(__file__).resolve().parent
_SR = 16_000


def _resample_24k_to_16k(pcm24: bytes) -> bytes:
    x = np.frombuffer(pcm24, dtype=np.int16).astype(np.float32)
    y = resample_poly(x, up=2, down=3)  # 24000 × 2/3 = 16000
    return np.clip(np.round(y), -32768, 32767).astype(np.int16).tobytes()


async def _text(item: str) -> AsyncIterator[str]:
    yield item


async def _render(text: str) -> bytes:
    from persona_voice.tts import ResolvedVoice, StreamingTTSConfig
    from persona_voice.tts.cartesia_backend import CartesiaStreamingTTS

    voice_id = os.environ["PERSONA_TTS_VOICE_DEFAULT"]
    backend = CartesiaStreamingTTS(
        StreamingTTSConfig(
            provider="cartesia",
            api_key=os.environ["PERSONA_TTS_API_KEY"],  # type: ignore[arg-type]
            voice_default=voice_id,
        )
    )
    voice = ResolvedVoice(provider="cartesia", voice_ref=voice_id)
    pcm24 = bytearray()
    try:
        async for chunk in backend.synthesize(_text(text), voice):
            pcm24 += chunk.data
    finally:
        await backend.close()
    return _resample_24k_to_16k(bytes(pcm24))


async def main() -> int:
    if not os.environ.get("PERSONA_TTS_API_KEY") or not os.environ.get("PERSONA_TTS_VOICE_DEFAULT"):
        print(
            "set PERSONA_TTS_API_KEY + PERSONA_TTS_VOICE_DEFAULT "
            "(and SSL_CERT_FILE=$(python -m certifi))"
        )
        return 2
    from persona_voice.stt.cost_harness import V8_CORPUS

    for u in V8_CORPUS:
        pcm = await _render(u.text)
        (_OUT / f"{u.utterance_id}.raw").write_bytes(pcm)
        print(f"  {u.utterance_id:14s} {len(pcm) / 2 / _SR:5.2f}s  ({u.stress})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
