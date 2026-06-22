"""Unit tests for the Spec V8 cost harness (``persona_voice.stt.cost_harness``).

Covers Axis 1 (streamed-seconds saved), deterministic + CI-safe:

1. The arithmetic model — orderings, ratios, the representative listen-heavy
   point estimate, and the cost re-base.
2. **The gate-faithful validation** — drive the *real* shipped
   :class:`PersonaSpeakingGate` over the same timeline through a real seam
   adapter + recording backend, and assert the measured streamed-seconds equals
   the B1 model. This is what makes the modelled numbers trustworthy: the gate
   logic is real, only the call *profile* is modelled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona_voice.logging import compute_stt_total_cents
from persona_voice.stt.cost_gate import IdleAwareGate, PersonaSpeakingGate
from persona_voice.stt.cost_harness import (
    DEEPGRAM_PAYG_CENTS_PER_MINUTE,
    LISTEN_HEAVY_PROFILE,
    V8_CORPUS,
    CostMeasurement,
    iter_segment_frames,
    summarize_cost,
)
from persona_voice.stt.seam_adapter import V1STTStreamSeamAdapter
from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona_voice.loop.streaming import Transcript


# ---------- doubles ---------------------------------------------------------


class _RecordingBackend:
    """StreamingSTT double — records billed bytes; empty event/transcript streams."""

    def __init__(self) -> None:
        self.total_bytes = 0
        self.sample_rate = 0
        self.closed = False

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        self.total_bytes += len(pcm)
        self.sample_rate = sample_rate

    async def transcripts(self) -> AsyncIterator[Transcript]:
        return
        yield  # pragma: no cover

    async def speech_activity_events(self) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        return
        yield  # pragma: no cover

    async def close(self) -> None:
        self.closed = True


class _NullVAD:
    """SileroVADAdapter double — accepts frames, emits no activity."""

    def __init__(self) -> None:
        self.closed = False

    async def load(self) -> None:  # pragma: no cover — unused here
        pass

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        pass

    async def speech_activity_events(self) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        return
        yield  # pragma: no cover

    async def close(self) -> None:
        self.closed = True


class _SettableSpeaking:
    """AgentSpeakingSource the harness toggles per segment label."""

    def __init__(self) -> None:
        self.speaking = False

    def is_agent_speaking(self) -> bool:
        return self.speaking


class _SettableUserTurn:
    """UserTurnSource the harness toggles per segment label."""

    def __init__(self) -> None:
        self.active = False

    def is_user_turn_active(self) -> bool:
        return self.active


# ---------- the arithmetic model --------------------------------------------


def test_summarize_orderings_and_ratio_identities() -> None:
    m = summarize_cost()
    # Monotone ladder: A ≥ B1 ≥ B1+idle.
    assert m.s_a_seconds >= m.s1_state_gate_seconds >= m.s1_idle_seconds
    # B1's saving IS the persona fraction (the general law).
    assert m.b1_saving_ratio == pytest.approx(m.persona_seconds / m.total_seconds)
    # Idle gating's marginal IS the idle fraction.
    assert m.idle_marginal_ratio == pytest.approx(m.idle_seconds / m.total_seconds)
    # B1+idle saving = B1 + idle marginal.
    assert m.b1_idle_saving_ratio == pytest.approx(m.b1_saving_ratio + m.idle_marginal_ratio)


def test_listen_heavy_point_estimate() -> None:
    """Pin the representative numbers reported at the measure-STOP."""
    m = summarize_cost(LISTEN_HEAVY_PROFILE)
    assert m.total_seconds == pytest.approx(296.0)
    assert m.persona_seconds == pytest.approx(233.0)
    assert m.idle_seconds == pytest.approx(18.0)
    assert m.s_a_seconds == pytest.approx(296.0)
    assert m.s1_state_gate_seconds == pytest.approx(63.0)
    assert m.s1_idle_seconds == pytest.approx(45.0)
    assert m.b1_saving_ratio == pytest.approx(233.0 / 296.0)  # ~0.787
    assert m.idle_marginal_ratio == pytest.approx(18.0 / 296.0)  # ~0.061


def test_cost_rebase_uses_streamed_seconds() -> None:
    m = summarize_cost(LISTEN_HEAVY_PROFILE)
    assert m.cents_per_minute == DEEPGRAM_PAYG_CENTS_PER_MINUTE
    assert m.s_a_cents == pytest.approx(compute_stt_total_cents(296.0, 0.48))
    assert m.s1_state_gate_cents == pytest.approx(compute_stt_total_cents(63.0, 0.48))
    # B1 cents are far below the ungated baseline.
    assert m.s1_state_gate_cents < m.s_a_cents


def test_compute_stt_total_cents_helper() -> None:
    # 60 s at 0.48 ¢/min = exactly 0.48 ¢.
    assert compute_stt_total_cents(60.0, 0.48) == pytest.approx(0.48)
    assert compute_stt_total_cents(0.0, 0.48) == 0.0
    # Unset inputs → None (never a misleading zero).
    assert compute_stt_total_cents(None, 0.48) is None
    assert compute_stt_total_cents(60.0, None) is None


def test_corpus_covers_the_failure_modes() -> None:
    ids = {u.utterance_id for u in V8_CORPUS}
    assert ids == {"normal", "short", "mid_pause", "trailing_soft", "bargein"}


def test_iter_segment_frames_total_duration_matches_profile() -> None:
    m = summarize_cost(LISTEN_HEAVY_PROFILE)
    frames = list(iter_segment_frames(LISTEN_HEAVY_PROFILE, frame_ms=20.0))
    # Each frame is 20 ms; total frames × 20 ms == total call seconds.
    assert len(frames) * 0.020 == pytest.approx(m.total_seconds)
    # Every frame is a valid 20 ms PCM16/16 kHz slice (320 samples × 2 bytes).
    assert all(len(pcm) == 320 * 2 for _label, pcm in frames)


# ---------- the gate-faithful validation (real shipped gate vs the model) ----


@pytest.mark.asyncio
async def test_real_persona_speaking_gate_matches_b1_model() -> None:
    """Drive the REAL PersonaSpeakingGate over the timeline → equals S_1 model.

    This is what licenses trusting the modelled numbers: the gate logic is the
    shipped code, only the call profile is modelled.
    """
    model = summarize_cost(LISTEN_HEAVY_PROFILE)
    backend = _RecordingBackend()
    vad = _NullVAD()
    source = _SettableSpeaking()
    adapter = V1STTStreamSeamAdapter(
        backend=backend,  # type: ignore[arg-type]
        vad=vad,  # type: ignore[arg-type]
        gate=PersonaSpeakingGate(source=source),
    )
    for label, pcm in iter_segment_frames(LISTEN_HEAVY_PROFILE, frame_ms=20.0):
        # The orchestrator is PERSONA_SPEAKING exactly during persona segments.
        source.speaking = label == "persona"
        await adapter.push_audio(pcm, 16_000)
    await adapter.close()

    # The instrument and the recording backend agree, and both equal the model's
    # B1 (total − persona) streamed-seconds.
    backend_seconds = backend.total_bytes / (2 * 16_000)
    assert adapter.streamed_seconds == pytest.approx(model.s1_state_gate_seconds, abs=1e-6)
    assert backend_seconds == pytest.approx(model.s1_state_gate_seconds, abs=1e-6)


@pytest.mark.asyncio
async def test_real_idle_aware_gate_matches_b1_idle_model() -> None:
    """Drive the REAL shipped IdleAwareGate over the timeline → equals S_1+idle.

    Ring disabled (reopen_preroll_ms=0) so the measurement is the pure gate
    decision: stream only USER_SPEAKING + PROCESSING (= total − persona − idle).
    """
    model = summarize_cost(LISTEN_HEAVY_PROFILE)
    backend = _RecordingBackend()
    vad = _NullVAD()
    source = _SettableUserTurn()
    adapter = V1STTStreamSeamAdapter(
        backend=backend,  # type: ignore[arg-type]
        vad=vad,  # type: ignore[arg-type]
        gate=IdleAwareGate(source=source),
    )
    for label, pcm in iter_segment_frames(LISTEN_HEAVY_PROFILE, frame_ms=20.0):
        source.active = label in ("user", "processing")
        await adapter.push_audio(pcm, 16_000)
    await adapter.close()
    assert adapter.streamed_seconds == pytest.approx(model.s1_idle_seconds, abs=1e-6)


def test_cost_measurement_is_frozen() -> None:
    m = summarize_cost()
    assert isinstance(m, CostMeasurement)
    with pytest.raises(ValueError, match="frozen"):
        m.total_seconds = 1.0  # type: ignore[misc]
