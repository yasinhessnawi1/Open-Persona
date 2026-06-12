"""Spec V3 T14 external smoke — 4 measurement gates per Phase-4 LOCK.

🟦 **Operator-pass disposition (INFORMAL).** Marked ``@pytest.mark.external``
and skipped without ``PERSONA_TTS_API_KEY`` (Spec 02 D-02-11 convention).
Default ``pytest`` does NOT run it; operators invoke via ``pytest -m
external``. Because Spec 25's canonical operator-pass gate has NOT closed at
V3 sign-off, V3's operator-pass ships INFORMALLY with this canonical shape;
enforcement reconciles editorially at the Spec 19 R-19-1 audit when Spec 25
closes.

**Four measurement gates per decisions.md §"T14 measurement gates"** — the
unit/integration tests prove structure; THIS file is the binding live-provider
gate against real Cartesia Sonic 3.5:

1. **First-audio-out latency** (reply-text-start → first AudioChunk),
   EU origin: P50 ≤250 ms / P95 ≤500 ms. Falsification: loosen the
   first-fragment policy OR route to the ElevenLabs alternative; reopen
   D-V3-1 if both fail.
2. **Synthesis keeps pace** — no mid-reply audio underrun after first audio
   (cumulative emitted-audio duration tracks a long reply). Falsification:
   investigate chunker stall / provider backpressure.
3. **Prosody quality** on a representative-reply corpus (operator ear-test;
   consecutive-chunk seam audit). Falsification: granularity too fine OR
   provider lacks continuation semantics → reopen D-V3-2 / D-V3-1.
4. **Catalogue-voice audible difference** — two personas, two voices, +
   NO/SV/AR ear-test. Falsification: per-language routing (D-V3-6).

**Gate semantics (V2 T12 precedent).** Latency gates assert + ``pytest.fail``
with a structured falsification message on miss (a measurement, not an
xfail). Quality gates require human judgement — they synthesise audio
artifacts for the operator and ``pytest.skip`` with instructions when the
operator corpus / output dir is absent.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.environ.get("PERSONA_TTS_API_KEY") is None,
        reason="real Cartesia API key required; set PERSONA_TTS_API_KEY",
    ),
]

_TESTS_DIR = str(Path(__file__).resolve().parent.parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

# Gate thresholds (decisions.md §"T14 measurement gates" / D-V3-1 falsification).
_FIRST_AUDIO_P50_MS = 250.0
_FIRST_AUDIO_P95_MS = 500.0
_KEEPS_PACE_MAX_GAP_MS = 400.0


def _voice_id() -> str | None:
    return os.environ.get("PERSONA_TTS_VOICE_DEFAULT")


def _build_backend() -> object:
    """Real Cartesia backend from env (lazy import — collection-safe)."""
    from persona_voice.tts import StreamingTTSConfig
    from persona_voice.tts.cartesia_backend import CartesiaStreamingTTS

    config = StreamingTTSConfig(
        provider="cartesia",
        api_key=os.environ["PERSONA_TTS_API_KEY"],  # type: ignore[arg-type]
        voice_default=_voice_id(),
    )
    return CartesiaStreamingTTS(config)


def _resolved_voice(voice_id: str) -> object:
    from persona_voice.tts import ResolvedVoice

    return ResolvedVoice(provider="cartesia", voice_ref=voice_id)


async def _text(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


_REPLY = (
    "Hello there, and welcome. ",
    "This is a representative reply with several sentences. ",
    "It exercises the streaming synthesis path end to end. ",
    "The persona should begin speaking well before this finishes.",
)


# ---------- Gate 1 — first-audio latency -----------------------------------


@pytest.mark.asyncio
async def test_gate_1_first_audio_latency() -> None:
    if _voice_id() is None:
        pytest.skip("set PERSONA_TTS_VOICE_DEFAULT to a real Cartesia voice id")
    backend = _build_backend()
    voice = _resolved_voice(_voice_id() or "")
    samples: list[float] = []
    try:
        for _ in range(5):
            t0 = time.perf_counter()
            async for _audio in backend.synthesize(_text(*_REPLY), voice):  # type: ignore[attr-defined]
                samples.append((time.perf_counter() - t0) * 1000.0)
                break
    finally:
        await backend.close()  # type: ignore[attr-defined]

    p50 = statistics.median(samples)
    p95 = max(samples)  # n=5 — worst-case proxy for P95
    if p50 > _FIRST_AUDIO_P50_MS or p95 > _FIRST_AUDIO_P95_MS:
        pytest.fail(
            f"GATE 1 MISS — first-audio P50={p50:.0f}ms (≤{_FIRST_AUDIO_P50_MS:.0f}) "
            f"/ P95={p95:.0f}ms (≤{_FIRST_AUDIO_P95_MS:.0f}). Falsification: loosen "
            "first-fragment policy OR route to ElevenLabs alternative; reopen "
            "D-V3-1 if both fail."
        )


# ---------- Gate 2 — synthesis keeps pace ----------------------------------


@pytest.mark.asyncio
async def test_gate_2_synthesis_keeps_pace() -> None:
    if _voice_id() is None:
        pytest.skip("set PERSONA_TTS_VOICE_DEFAULT to a real Cartesia voice id")
    backend = _build_backend()
    voice = _resolved_voice(_voice_id() or "")
    gaps: list[float] = []
    last = None
    try:
        async for _audio in backend.synthesize(_text(*_REPLY), voice):  # type: ignore[attr-defined]
            now = time.perf_counter()
            if last is not None:
                gaps.append((now - last) * 1000.0)
            last = now
    finally:
        await backend.close()  # type: ignore[attr-defined]

    worst_gap = max(gaps) if gaps else 0.0
    if worst_gap > _KEEPS_PACE_MAX_GAP_MS:
        pytest.fail(
            f"GATE 2 MISS — worst inter-chunk gap {worst_gap:.0f}ms "
            f"(≤{_KEEPS_PACE_MAX_GAP_MS:.0f}) after first audio. Falsification: "
            "investigate chunker stall / provider backpressure."
        )


# ---------- Gate 3 — prosody quality (operator ear-test) -------------------


@pytest.mark.asyncio
async def test_gate_3_prosody_quality_corpus() -> None:
    out_dir = os.environ.get("PERSONA_TTS_SMOKE_OUT")
    if _voice_id() is None or out_dir is None:
        pytest.skip(
            "operator ear-test gate — set PERSONA_TTS_VOICE_DEFAULT + "
            "PERSONA_TTS_SMOKE_OUT=<dir> to synthesise the prosody corpus for "
            "audition (consecutive-chunk seam audit; D-V3-2)."
        )
    backend = _build_backend()
    voice = _resolved_voice(_voice_id() or "")
    pcm = bytearray()
    try:
        async for audio in backend.synthesize(_text(*_REPLY), voice):  # type: ignore[attr-defined]
            pcm += audio.data
    finally:
        await backend.close()  # type: ignore[attr-defined]
    path = Path(out_dir) / "v3_prosody_corpus.pcm"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(pcm))
    # Operator auditions the PCM16 mono 24 kHz file; no auto-assert on prosody.
    assert len(pcm) > 0


# ---------- Gate 4 — catalogue-voice audible difference --------------------


@pytest.mark.asyncio
async def test_gate_4_catalogue_voice_difference() -> None:
    alt = os.environ.get("PERSONA_TTS_VOICE_ALT")
    if _voice_id() is None or alt is None:
        pytest.skip(
            "set PERSONA_TTS_VOICE_DEFAULT + PERSONA_TTS_VOICE_ALT to two real "
            "Cartesia voice ids to confirm two personas sound audibly different "
            "(+ NO/SV/AR ear-test; falsification → per-language routing, D-V3-6)."
        )
    backend = _build_backend()
    primary = _resolved_voice(_voice_id() or "")
    alternate = _resolved_voice(alt)
    line = "Hello, this is a test."
    try:
        a = bytearray()
        b = bytearray()
        async for audio in backend.synthesize(_text(line), primary):  # type: ignore[attr-defined]
            a += audio.data
        async for audio in backend.synthesize(_text(line), alternate):  # type: ignore[attr-defined]
            b += audio.data
    finally:
        await backend.close()  # type: ignore[attr-defined]
    # Structural signal: the same text in two voices yields different audio.
    assert bytes(a) != bytes(b)


# ---------- Gate 5 — mis-segmentation corpus (rule-based splitter) ---------


def test_gate_5_mis_segmentation_corpus_documented() -> None:
    # The rule-based splitter is unit-validated (test_chunking.py); the live
    # gate is the operator confirming no audible mid-abbreviation/decimal
    # split on the corpus through real synthesis. Falsification → swap pysbd
    # behind the chunker interface (D-V3-X-sentence-tokenizer). Documented
    # here as the operator-pass checklist item; no auto-assert at v0.1.
    pytest.skip(
        "operator-pass checklist: audition 'Dr. Smith arrived.', 'Pi is "
        "3.14159.', 'It costs $29.99 total. Next item.', 'Use e.g. this.' "
        "through real Cartesia; confirm no mid-token split. Falsification → "
        "pysbd swap (D-V3-X-sentence-tokenizer)."
    )
