"""Spec V8 — the empirical A/B cost harness (D-V8-3 / D-V8-6).

Two axes, two legs (research §4):

* **Axis 1 — streamed-seconds saved (cost).** Deterministic + CI-safe. The
  billed STT cost is a linear function of audio-seconds streamed to the
  provider, and the split-tee only forwards a frame to the backend when the
  gate is open — so the saving of a gating config is exactly the fraction of
  call audio its gate withholds. This module models a representative call as a
  labelled timeline (:data:`LISTEN_HEAVY_PROFILE`) and computes the
  streamed-seconds under each config (A / B1 / B1+idle). The numbers are
  **gate-faithful** (a unit test drives the *real* shipped
  :class:`PersonaSpeakingGate` over the same timeline and asserts it matches
  the B1 model) — what is *modelled* is the call profile, not the gate logic.

* **Axis 2 — transcript fidelity (WER vs A, first-word, endpoint integrity).**
  Needs a live provider; runs in the external leg (T4) over the rendered
  :data:`V8_CORPUS`. Out of scope for this CI-safe module.

The configs (research §4.2 ladder, monotonically more saving):

* **A** — ungated: stream the whole call. ``S_A = total``.
* **B1** — #1 state-gate (D-V8-2): withhold while ``PERSONA_SPEAKING``.
  ``S_1 = total − persona``. (The shipped path.)
* **B1+idle** — also withhold ``LISTENING`` idle (D-V8-7, ship-decision
  deferred to T4). ``S_1_idle = total − persona − idle``.

The general law (reported alongside the point estimate): **#1's saving equals
the persona-speaking fraction of the call** — because #1 gates exactly
``PERSONA_SPEAKING``. The listen-heavier the call, the larger the win.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from persona_voice.logging import compute_stt_total_cents

__all__ = [
    "DEEPGRAM_PAYG_CENTS_PER_MINUTE",
    "LISTEN_HEAVY_PROFILE",
    "V8_CORPUS",
    "CallSegment",
    "CorpusUtterance",
    "CostMeasurement",
    "iter_segment_frames",
    "summarize_cost",
]

SegmentLabel = Literal["persona", "user", "idle", "processing"]

# Deepgram Nova-3 streaming PAYG, Phase-3-corrected (D-V2-X-cost-discipline):
# $0.0048/min = 0.48 cents/min. Growth tier is 0.42 ¢/min.
DEEPGRAM_PAYG_CENTS_PER_MINUTE: float = 0.48

# 16 kHz mono PCM16 — the D-V1-6 inbound-rail invariant the corpus + harness use.
_SAMPLE_RATE_HZ: int = 16_000
_BYTES_PER_SAMPLE: int = 2


class CallSegment(BaseModel):
    """One labelled stretch of a modelled call (frozen)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: SegmentLabel
    duration_ms: float = Field(gt=0.0)


# A representative listen-heavy call (~296 s): an opening greeting, then ~9
# exchanges of {idle, short user turn, brief processing, long persona reply}.
# Persona audio dominates — the case V8 targets. Durations are the modelled
# assumption; the gate logic applied to them is real.
_OPENING_GREETING = (CallSegment(label="persona", duration_ms=8_000.0),)
_EXCHANGE = (
    CallSegment(label="idle", duration_ms=2_000.0),
    CallSegment(label="user", duration_ms=4_000.0),
    CallSegment(label="processing", duration_ms=1_000.0),
    CallSegment(label="persona", duration_ms=25_000.0),
)
LISTEN_HEAVY_PROFILE: tuple[CallSegment, ...] = _OPENING_GREETING + _EXCHANGE * 9


class CorpusUtterance(BaseModel):
    """One fixed fidelity-stress utterance for the Axis-2 live leg (frozen).

    Rendered to PCM16/16 kHz ``.raw`` by ``tests/fixtures/v8_corpus/generate_corpus.py``
    (Cartesia, on-demand, needs a key); replayed through the real Deepgram
    backend in the external T4 leg. ``stress`` names the failure mode the
    utterance exercises (research §4.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    utterance_id: str
    text: str
    stress: str
    language: str = "en"


V8_CORPUS: tuple[CorpusUtterance, ...] = (
    CorpusUtterance(
        utterance_id="normal",
        text="I was thinking we could go over the quarterly numbers together this afternoon.",
        stress="baseline fidelity",
    ),
    CorpusUtterance(
        utterance_id="short",
        text="Stop.",
        stress="first-word clipping (pre-roll)",
    ),
    CorpusUtterance(
        utterance_id="mid_pause",
        text="So the plan is... give me a second... the plan is to ship on Friday.",
        stress="false-offset (mid-thought pause)",
    ),
    CorpusUtterance(
        utterance_id="trailing_soft",
        text="Yeah, that works for me, I think.",
        stress="trailing-word clipping (hold-over / Finalize)",
    ),
    CorpusUtterance(
        utterance_id="bargein",
        text="Wait, no, that's not what I meant.",
        stress="barge-in re-open latency (pre-roll covers the gap)",
    ),
)


class CostMeasurement(BaseModel):
    """Axis-1 result for one call profile (frozen)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_seconds: float = Field(ge=0.0)
    persona_seconds: float = Field(ge=0.0)
    idle_seconds: float = Field(ge=0.0)

    s_a_seconds: float = Field(ge=0.0)
    s1_state_gate_seconds: float = Field(ge=0.0)
    s1_idle_seconds: float = Field(ge=0.0)

    b1_saving_ratio: float = Field(ge=0.0, le=1.0)
    b1_idle_saving_ratio: float = Field(ge=0.0, le=1.0)
    idle_marginal_ratio: float = Field(ge=0.0, le=1.0)

    cents_per_minute: float = Field(ge=0.0)
    s_a_cents: float = Field(ge=0.0)
    s1_state_gate_cents: float = Field(ge=0.0)
    s1_idle_cents: float = Field(ge=0.0)


def _seconds_by_label(profile: tuple[CallSegment, ...]) -> dict[SegmentLabel, float]:
    totals: dict[SegmentLabel, float] = {
        "persona": 0.0,
        "user": 0.0,
        "idle": 0.0,
        "processing": 0.0,
    }
    for seg in profile:
        totals[seg.label] += seg.duration_ms / 1000.0
    return totals


def summarize_cost(
    profile: tuple[CallSegment, ...] = LISTEN_HEAVY_PROFILE,
    *,
    cents_per_minute: float = DEEPGRAM_PAYG_CENTS_PER_MINUTE,
) -> CostMeasurement:
    """Streamed-seconds + cost for A / B1 / B1+idle over ``profile`` (Axis 1).

    ``S_A = total``; ``S_1 = total − persona`` (#1 gates ``PERSONA_SPEAKING``);
    ``S_1_idle = total − persona − idle``. Saving ratios are fractions of the
    ungated baseline; ``idle_marginal_ratio`` is what idle-gating adds *beyond*
    #1 (the number D-V8-7 weighs). Cost via :func:`compute_stt_total_cents`.
    """
    by = _seconds_by_label(profile)
    total = sum(by.values())
    persona = by["persona"]
    idle = by["idle"]
    s_a = total
    s_1 = total - persona
    s_1_idle = total - persona - idle

    def _cents(seconds: float) -> float:
        # cents_per_minute is non-None here, so the helper returns a float.
        return compute_stt_total_cents(seconds, cents_per_minute) or 0.0

    return CostMeasurement(
        total_seconds=total,
        persona_seconds=persona,
        idle_seconds=idle,
        s_a_seconds=s_a,
        s1_state_gate_seconds=s_1,
        s1_idle_seconds=s_1_idle,
        b1_saving_ratio=(s_a - s_1) / s_a if s_a else 0.0,
        b1_idle_saving_ratio=(s_a - s_1_idle) / s_a if s_a else 0.0,
        idle_marginal_ratio=(s_1 - s_1_idle) / s_a if s_a else 0.0,
        cents_per_minute=cents_per_minute,
        s_a_cents=_cents(s_a),
        s1_state_gate_cents=_cents(s_1),
        s1_idle_cents=_cents(s_1_idle),
    )


def iter_segment_frames(
    profile: tuple[CallSegment, ...] = LISTEN_HEAVY_PROFILE,
    *,
    frame_ms: float = 20.0,
) -> Iterator[tuple[SegmentLabel, bytes]]:
    """Yield ``(label, pcm16_frame)`` for each ``frame_ms`` slice of ``profile``.

    Deterministic silence frames (the streamed-seconds count is content-
    agnostic — the gate decides by conversational state, not audio). A test
    replays these through the *real* seam adapter + gate to validate the B1
    model against the shipped gate. ``frame_ms`` must divide each segment.
    """
    frame_samples = int(_SAMPLE_RATE_HZ * frame_ms / 1000.0)
    frame = b"\x00\x00" * frame_samples
    for seg in profile:
        n_frames = round(seg.duration_ms / frame_ms)
        for _ in range(n_frames):
            yield seg.label, frame
