"""Full-loop latency attribution (spec V4 T08 — criterion #6, D-V4-X-eou-stamp-point).

V4 owns the user-perceived round-trip number and is accountable for it (spec
§3). It does not re-instrument — V1's :class:`persona_voice.logging.VoiceLog`
already carries the five hop anchors. This module turns those anchors into an
**attribution** so a too-slow loop is diagnosable to the owning hop, and records
the endpoint-silence-wait as a **separate line** so the threshold's latency cost
is never hidden (the Phase-2 refinement to D-V4-X-eou-stamp-point):

* the **processing round-trip** (``eou_at`` → ``audio_first_play_at``) is the
  number measured against V1's 800 ms P50 / 1.5 s P95 budget — the
  pipeline-vs-budget apples-to-apples comparison (R-V1-3);
* the **endpoint-silence-wait** (raw user-offset → the ``END_TURN`` decision,
  i.e. the tuned silence threshold) is recorded separately — it is a V4 *choice*,
  not a pipeline regression;
* the **user-perceived total** = silence-wait + processing round-trip is
  reconstructable from the two.

``eou_at`` is stamped at the controller's ``END_TURN`` decision (not the raw
Silero offset), so the threshold falls inside the silence-wait line, not the
budget number.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from persona_voice.logging import compute_e2e_ms

if TYPE_CHECKING:
    from datetime import datetime

    from persona_voice.logging import VoiceLog

__all__ = ["HopAttribution", "attribute_hops", "compute_full_loop_ms"]


class HopAttribution(BaseModel):
    """Per-hop latency breakdown of one turn (frozen; ms; ``None`` if unanchored).

    Every field is ``None`` when either of its bounding anchors is unset (a
    partially-instrumented turn), so the attribution degrades gracefully rather
    than fabricating a number.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Pipeline hops (each blamed on the owning spec when over budget).
    stt_final_ms: float | None = None
    """``eou_at`` → ``stt_final_at`` (V2)."""
    model_first_token_ms: float | None = None
    """``stt_final_at`` → ``llm_first_token_at`` (V5)."""
    tts_first_byte_ms: float | None = None
    """``llm_first_token_at`` → ``tts_first_byte_at`` (V3)."""
    transport_playout_ms: float | None = None
    """``tts_first_byte_at`` → ``audio_first_play_at`` (V1)."""

    # The budget number + the dual-line silence-wait + the user-perceived total.
    processing_round_trip_ms: float | None = None
    """``eou_at`` → ``audio_first_play_at`` — checked against the 800 ms / 1.5 s budget."""
    endpoint_silence_wait_ms: float | None = None
    """Raw user-offset → END_TURN (the tuned silence threshold) — a V4 choice, shown not hidden."""
    user_perceived_total_ms: float | None = None
    """silence-wait + processing round-trip — what the user actually felt."""


def _delta_ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds() * 1000.0


def compute_full_loop_ms(log: VoiceLog) -> float | None:
    """The processing round-trip (``eou_at`` → ``audio_first_play_at``) in ms.

    The V4-owned number checked against R-V1-3's 800 ms P50 / 1.5 s P95 budget.
    Thin alias of :func:`persona_voice.logging.compute_e2e_ms` under V4's name.
    """
    return compute_e2e_ms(log)


def attribute_hops(
    log: VoiceLog,
    *,
    endpoint_silence_wait_ms: float | None = None,
) -> HopAttribution:
    """Break one turn's VoiceLog into a per-hop :class:`HopAttribution`.

    Args:
        log: The turn's latency record (V2/V3/V5 stamp their hops; V4 stamps
            ``eou_at`` at the END_TURN decision per D-V4-X-eou-stamp-point).
        endpoint_silence_wait_ms: The silence-threshold wait the controller
            measured for this turn (raw offset → END_TURN). The orchestrator
            supplies it from the turn's :class:`TurnDecision`; ``None`` if not
            measured. Recorded as its own line, never folded into the budget
            number.

    Returns:
        A :class:`HopAttribution`; fields are ``None`` where anchors are unset.
    """
    processing = compute_e2e_ms(log)
    user_perceived = (
        endpoint_silence_wait_ms + processing
        if endpoint_silence_wait_ms is not None and processing is not None
        else None
    )
    return HopAttribution(
        stt_final_ms=_delta_ms(log.eou_at, log.stt_final_at),
        model_first_token_ms=_delta_ms(log.stt_final_at, log.llm_first_token_at),
        tts_first_byte_ms=_delta_ms(log.llm_first_token_at, log.tts_first_byte_at),
        transport_playout_ms=_delta_ms(log.tts_first_byte_at, log.audio_first_play_at),
        processing_round_trip_ms=processing,
        endpoint_silence_wait_ms=endpoint_silence_wait_ms,
        user_perceived_total_ms=user_perceived,
    )
