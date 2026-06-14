"""Unit tests for full-loop latency attribution (spec V4 T08)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from persona_voice.logging import VoiceLog
from persona_voice.turn_taking.latency import (
    HopAttribution,
    attribute_hops,
    compute_full_loop_ms,
)

_BASE = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


def _at(ms: float) -> datetime:
    return _BASE + timedelta(milliseconds=ms)


def _fully_anchored_log() -> VoiceLog:
    """A turn with all five hop anchors set (realistic sub-budget numbers)."""
    return VoiceLog(
        session_id="s",
        user_id="u",
        persona_id="p",
        conversation_id="c",
        turn_index=0,
        started_at=_BASE,
        eou_at=_at(0.0),
        stt_final_at=_at(120.0),
        llm_first_token_at=_at(400.0),
        tts_first_byte_at=_at(560.0),
        audio_first_play_at=_at(640.0),
    )


def test_attribute_hops_breaks_down_each_pipeline_hop() -> None:
    attrib = attribute_hops(_fully_anchored_log())
    assert attrib.stt_final_ms == pytest.approx(120.0)
    assert attrib.model_first_token_ms == pytest.approx(280.0)
    assert attrib.tts_first_byte_ms == pytest.approx(160.0)
    assert attrib.transport_playout_ms == pytest.approx(80.0)
    # Processing round-trip = eou → first audio = 640 ms (sub-800 ms budget).
    assert attrib.processing_round_trip_ms == pytest.approx(640.0)


def test_compute_full_loop_ms_is_the_processing_round_trip() -> None:
    assert compute_full_loop_ms(_fully_anchored_log()) == pytest.approx(640.0)


def test_dual_line_silence_wait_and_user_perceived_total() -> None:
    """D-V4-X-eou-stamp-point — the threshold cost is a separate line, and the
    user-perceived total is reconstructable."""
    attrib = attribute_hops(_fully_anchored_log(), endpoint_silence_wait_ms=700.0)
    assert attrib.endpoint_silence_wait_ms == pytest.approx(700.0)
    # User felt the silence-wait PLUS the processing round-trip.
    assert attrib.user_perceived_total_ms == pytest.approx(1340.0)
    # ...but the budget number itself excludes the threshold (not hidden, not folded in).
    assert attrib.processing_round_trip_ms == pytest.approx(640.0)


def test_partial_log_degrades_to_none_not_fabrication() -> None:
    """A turn that ended before the agent spoke has no round-trip number."""
    log = VoiceLog(
        session_id="s",
        user_id="u",
        persona_id="p",
        conversation_id="c",
        turn_index=0,
        started_at=_BASE,
        eou_at=_at(0.0),
        stt_final_at=_at(120.0),
        # no model/tts/audio anchors
    )
    attrib = attribute_hops(log)
    assert attrib.stt_final_ms == pytest.approx(120.0)
    assert attrib.model_first_token_ms is None
    assert attrib.tts_first_byte_ms is None
    assert attrib.transport_playout_ms is None
    assert attrib.processing_round_trip_ms is None
    assert attrib.user_perceived_total_ms is None


def test_no_silence_wait_means_no_user_perceived_total() -> None:
    attrib = attribute_hops(_fully_anchored_log(), endpoint_silence_wait_ms=None)
    assert attrib.user_perceived_total_ms is None
    assert attrib.processing_round_trip_ms == pytest.approx(640.0)


def test_attribution_is_frozen() -> None:
    from pydantic import ValidationError

    attrib = attribute_hops(_fully_anchored_log())
    assert isinstance(attrib, HopAttribution)
    with pytest.raises(ValidationError):
        HopAttribution(stt_final_ms=1.0, bogus=2.0)  # type: ignore[call-arg]
