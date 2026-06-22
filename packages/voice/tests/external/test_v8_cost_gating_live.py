"""Spec V8 T4b — committed live-leg replay (real Deepgram; the durable fidelity gate).

🟦 **Operator-pass disposition (synthetic-corpus relative-WER mechanism check).**
Marked ``@pytest.mark.external`` + skipped without ``PERSONA_STT_API_KEY`` — default
``pytest`` excludes it (``-m 'not external'``); run with
``SSL_CERT_FILE=$(python -m certifi) pytest -m external -k v8_cost_gating``. This is
**NOT** the Phase-5 real-human operator pass (real voice, real session) — it is the
reproducible measure-don't-assert artifact behind the #2 decline + the barge-in ring
fix (D-V8-X-measure-stop-verdict), so a future gate/ring change can be re-validated
against real Deepgram instead of a lost one-time script.

**Deterministic on the Deepgram side via committed fixtures.** The corpus is replayed
from ``tests/fixtures/v8_corpus/*.raw`` (PCM16/16 kHz mono, rendered once by
``generate_corpus.py`` — no Cartesia re-render variance). The assertion is **relative**:
the gated+ring transcript must match the *ungated A baseline computed in the same run*
(first-word preserved, WER ≤ A + 2.0 pp — the D-V8-5 bars), so it is robust to any
run-to-run Deepgram drift. Utterances whose A baseline itself yields no FINAL (a
render/replay artifact, not a gating effect) are skipped, not failed.

The two scenarios exercise the ONE shared ring-buffer-on-reopen:
* **barge-in** — gate closed for the first ~250 ms (the confirm window) → ring flush.
* **idle**     — gate closed for the first ~100 ms (the onset lag)      → ring flush.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import certifi
import pytest

# Make the live providers reachable out of the box on hosts whose Python trust
# store lacks the CA chain (the T4b finding) — only set if the operator hasn't.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.environ.get("PERSONA_STT_API_KEY") is None,
        reason="real Deepgram API key required; set PERSONA_STT_API_KEY",
    ),
    pytest.mark.asyncio,
]

_CORPUS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "v8_corpus"
_SR = 16_000
_FRAME_MS = 20
_FRAME_BYTES = _SR * _FRAME_MS // 1000 * 2
_WER_TOLERANCE = 0.02  # D-V8-5: WER ≤ A + 2.0 pp


def _make_dg() -> object:
    from persona_voice.stt.config import StreamingSTTConfig
    from persona_voice.stt.deepgram_backend import DeepgramStreamingSTT

    return DeepgramStreamingSTT(
        StreamingSTTConfig(
            provider="deepgram",
            model="nova-3",
            api_key=os.environ["PERSONA_STT_API_KEY"],  # type: ignore[arg-type]
            language_hint="en",
            vad_library="silero",
        )
    )


def _silence(ms: int) -> bytes:
    return b"\x00\x00" * (_SR * ms // 1000)


def _frames(pcm: bytes) -> list[bytes]:
    return [pcm[i : i + _FRAME_BYTES] for i in range(0, len(pcm), _FRAME_BYTES)]


async def _collect_finals(backend: object, push_coro: object) -> str:
    finals: list[str] = []

    async def drain() -> None:
        async for t in backend.transcripts():  # type: ignore[attr-defined]
            if t.is_final and t.text:
                finals.append(t.text)

    task = asyncio.create_task(drain())
    await push_coro
    await asyncio.sleep(2.5)  # let endpointing emit the final(s)
    await backend.close()  # type: ignore[attr-defined]
    try:
        await asyncio.wait_for(task, timeout=3.0)
    except (TimeoutError, asyncio.CancelledError):
        task.cancel()
    return " ".join(finals).strip()


async def _transcribe_ungated(pcm: bytes) -> str:
    dg = _make_dg()

    async def push() -> None:
        for fr in _frames(pcm):
            await dg.push_audio(fr, _SR)  # type: ignore[attr-defined]
        await dg.push_audio(_silence(700), _SR)  # type: ignore[attr-defined]

    return await _collect_finals(dg, push())


async def _transcribe_gated_ring(pcm: bytes, *, closed_ms: int) -> str:
    """Replay through the REAL seam adapter: gate closed for the first closed_ms,
    then reopen (the 300 ms ring flushes the buffered run-up), then the rest."""
    from persona_voice.stt.seam_adapter import V1STTStreamSeamAdapter

    class _NullVAD:
        async def load(self) -> None: ...
        async def push_audio(self, pcm: bytes, sr: int) -> None: ...
        async def speech_activity_events(self) -> AsyncIterator[object]:
            return
            yield

        async def close(self) -> None: ...

    class _Toggle:
        def __init__(self) -> None:
            self.open = False

        def is_open(self) -> bool:
            return self.open

    dg = _make_dg()
    gate = _Toggle()
    seam = V1STTStreamSeamAdapter(
        backend=dg,  # type: ignore[arg-type]
        vad=_NullVAD(),  # type: ignore[arg-type]
        gate=gate,
        reopen_preroll_ms=300.0,
    )
    frames = _frames(pcm)
    closed_frames = max(1, closed_ms // _FRAME_MS)

    async def push() -> None:
        for i, fr in enumerate(frames):
            gate.open = i >= closed_frames
            await seam.push_audio(fr, _SR)
        gate.open = True
        await seam.push_audio(_silence(700), _SR)

    return await _collect_finals(dg, push())


def _word_edit_distance(ref: list[str], hyp: list[str]) -> int:
    m, n = len(ref), len(hyp)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[m][n]


def _wer(ref: str, hyp: str) -> float:
    r = ref.lower().split()
    return _word_edit_distance(r, hyp.lower().split()) / len(r) if r else 0.0


def _first_word(s: str) -> str:
    toks = s.split()
    return toks[0].lower() if toks else ""


def _fixture_ids() -> list[str]:
    if not _CORPUS_DIR.exists():
        return []
    return sorted(p.stem for p in _CORPUS_DIR.glob("*.raw"))


@pytest.mark.parametrize("utterance_id", _fixture_ids() or ["__missing__"])
@pytest.mark.parametrize(("scenario", "closed_ms"), [("bargein", 250), ("idle", 100)])
async def test_gated_ring_matches_ungated_baseline(
    utterance_id: str, scenario: str, closed_ms: int
) -> None:
    """Gated+ring transcription must equal the ungated A baseline (D-V8-5 bars).

    The reopen-clip fix: a gate closed for the run-up then reopened (ring flush)
    must transcribe identically to never gating — first-word preserved, WER within
    +2.0 pp, a FINAL present. Robust to Deepgram drift (A is recomputed per run).
    """
    if utterance_id == "__missing__":
        pytest.skip(f"no rendered corpus at {_CORPUS_DIR}; run generate_corpus.py first")
    pcm = (_CORPUS_DIR / f"{utterance_id}.raw").read_bytes()

    baseline = await _transcribe_ungated(pcm)
    if not baseline:
        pytest.skip(
            f"[{utterance_id}] ungated A baseline yielded no FINAL — a render/replay "
            "artifact, not a gating signal; nothing to compare against"
        )

    gated = await _transcribe_gated_ring(pcm, closed_ms=closed_ms)

    assert gated, (
        f"[{utterance_id}/{scenario}] gated+ring yielded NO FINAL while ungated A "
        f"did ({baseline!r}) — the reopen flush failed to deliver the utterance"
    )
    assert _first_word(gated) == _first_word(baseline), (
        f"[{utterance_id}/{scenario}] first-word clipped: A={baseline!r} gated={gated!r}"
    )
    wer = _wer(baseline, gated)
    assert wer <= _WER_TOLERANCE + 1e-9, (
        f"[{utterance_id}/{scenario}] WER {wer:.1%} > {_WER_TOLERANCE:.1%} vs A — "
        f"A={baseline!r} gated={gated!r}"
    )
