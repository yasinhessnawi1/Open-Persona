"""Unit tests for :class:`VoiceLog` and :class:`JSONLVoiceLogWriter` (spec V1 T10).

Cover the frozen-Pydantic boundary contract, the LiveKit canonical hop
fields, V1's binding per-hop share, the JSON round-trip discipline (T05/T07
integration relies on this being lossless), the JSONL writer's append +
flush semantics, and the e2e / V1-share computed helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from persona_voice.logging import (
    V1_BUDGET_P50_MS,
    V1_BUDGET_P95_MS,
    JSONLVoiceLogWriter,
    VoiceLog,
    compute_e2e_ms,
    compute_v1_share_ms,
)
from pydantic import ValidationError

# ---------- helpers ---------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)


def _build_log(**overrides: object) -> VoiceLog:
    defaults = {
        "session_id": "sess_1",
        "user_id": "user_a",
        "persona_id": "p_astrid",
        "conversation_id": "c_chat",
        "turn_index": 0,
        "started_at": _now(),
    }
    defaults.update(overrides)
    return VoiceLog(**defaults)  # type: ignore[arg-type]


# ---------- VoiceLog boundary contract -------------------------------------


def test_voice_log_is_frozen_and_extra_forbid() -> None:
    log = _build_log()
    with pytest.raises(ValidationError):
        VoiceLog(  # type: ignore[call-arg]
            session_id="s",
            user_id="u",
            persona_id="p",
            conversation_id="c",
            turn_index=0,
            started_at=_now(),
            unknown_field="x",
        )
    # frozen: post-construction assignment raises
    with pytest.raises(ValidationError):
        log.turn_index = 99  # type: ignore[misc]


def test_turn_index_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        _build_log(turn_index=-1)


def test_v1_per_hop_ms_fields_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        _build_log(transport_in_ms=-1.0)
    with pytest.raises(ValidationError):
        _build_log(loop_overhead_ms=-0.5)
    with pytest.raises(ValidationError):
        _build_log(transport_out_ms=-3.0)


def test_all_hop_fields_default_to_none() -> None:
    """V1 ships the framework; V2/V3/V5 populate their own hops as they
    land. Missing hops must round-trip as None, not crash validation."""
    log = _build_log()
    assert log.eou_at is None
    assert log.stt_final_at is None
    assert log.llm_first_token_at is None
    assert log.tts_first_byte_at is None
    assert log.audio_first_play_at is None
    assert log.transport_in_ms is None
    assert log.transport_out_ms is None
    assert log.loop_overhead_ms is None


def test_livekit_canonical_hop_fields_are_named_per_d_v1_x_coordination() -> None:
    """Field-name stability is the cross-spec coordination contract
    (D-V1-X-first-token-measurement-coordination). Spec 18 records the
    same ``llm_first_token_at`` shape; V5 reads from both producers."""
    fields = set(VoiceLog.model_fields.keys())
    expected_canonical = {
        "eou_at",
        "stt_final_at",
        "llm_first_token_at",
        "tts_first_byte_at",
        "audio_first_play_at",
    }
    assert expected_canonical <= fields


# ---------- JSON round-trip ------------------------------------------------


def test_voice_log_json_round_trips_losslessly() -> None:
    eou = _now()
    play = eou + timedelta(milliseconds=720)
    log = _build_log(
        ended_at=play,
        eou_at=eou,
        stt_final_at=eou + timedelta(milliseconds=200),
        llm_first_token_at=eou + timedelta(milliseconds=420),
        tts_first_byte_at=eou + timedelta(milliseconds=620),
        audio_first_play_at=play,
        transport_in_ms=12.5,
        transport_out_ms=18.3,
        loop_overhead_ms=22.1,
    )
    blob = log.model_dump_json()
    parsed = VoiceLog.model_validate_json(blob)
    assert parsed == log
    # The serialized form is one line of JSON (no embedded newlines).
    assert "\n" not in blob


# ---------- computed helpers -----------------------------------------------


def test_compute_e2e_ms_returns_user_perceived_round_trip() -> None:
    eou = _now()
    play = eou + timedelta(milliseconds=750)
    log = _build_log(eou_at=eou, audio_first_play_at=play)
    assert compute_e2e_ms(log) == pytest.approx(750.0)


def test_compute_e2e_ms_returns_none_when_either_anchor_unset() -> None:
    assert compute_e2e_ms(_build_log()) is None
    assert compute_e2e_ms(_build_log(eou_at=_now())) is None
    assert compute_e2e_ms(_build_log(audio_first_play_at=_now())) is None


def test_compute_v1_share_ms_sums_three_hops() -> None:
    log = _build_log(
        transport_in_ms=25.0,
        loop_overhead_ms=30.0,
        transport_out_ms=28.0,
    )
    assert compute_v1_share_ms(log) == pytest.approx(83.0)


def test_compute_v1_share_ms_returns_none_when_any_hop_unset() -> None:
    """A partial measurement can't be checked against the binding contract —
    return None so the CI gate skips uninstrumented turns gracefully."""
    assert compute_v1_share_ms(_build_log()) is None
    assert compute_v1_share_ms(_build_log(transport_in_ms=10.0)) is None
    assert compute_v1_share_ms(_build_log(transport_in_ms=10.0, loop_overhead_ms=20.0)) is None


def test_v1_share_budget_constants_match_d_v1_x_latency_target() -> None:
    """The binding 100ms P50 / 150ms P95 contract from D-V1-X-latency-target."""
    assert V1_BUDGET_P50_MS == 100
    assert V1_BUDGET_P95_MS == 150


def test_v1_share_within_p50_budget_passes_the_gate() -> None:
    """A turn fully under the binding share — the happy path T08/T11
    integration tests assert at wall-clock level."""
    log = _build_log(
        transport_in_ms=30.0,
        loop_overhead_ms=40.0,
        transport_out_ms=30.0,
    )
    share = compute_v1_share_ms(log)
    assert share is not None
    assert share <= V1_BUDGET_P50_MS


def test_v1_share_outside_p95_budget_fails_the_gate() -> None:
    """A turn whose V1 share blew through the binding contract — operator
    pass would flag this at close-out."""
    log = _build_log(
        transport_in_ms=80.0,
        loop_overhead_ms=80.0,
        transport_out_ms=80.0,
    )
    share = compute_v1_share_ms(log)
    assert share is not None
    assert share > V1_BUDGET_P95_MS


# ---------- JSONLVoiceLogWriter --------------------------------------------


@pytest.mark.asyncio
async def test_jsonl_writer_appends_one_line_per_write(tmp_path: Path) -> None:
    path = tmp_path / "voice.jsonl"
    writer = JSONLVoiceLogWriter(path)
    await writer.write(_build_log(turn_index=0))
    await writer.write(_build_log(turn_index=1, transport_in_ms=12.0))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    # Each line is a complete JSON object.
    import json as _json

    first = _json.loads(lines[0])
    second = _json.loads(lines[1])
    assert first["turn_index"] == 0
    assert second["turn_index"] == 1
    assert second["transport_in_ms"] == 12.0


@pytest.mark.asyncio
async def test_jsonl_writer_creates_parent_directory(tmp_path: Path) -> None:
    """Reduces deploy surprises: a long-running session that first writes
    after the directory got reaped should not crash with FileNotFoundError."""
    nested = tmp_path / "deep" / "nested" / "voice.jsonl"
    writer = JSONLVoiceLogWriter(nested)
    await writer.write(_build_log())
    assert nested.exists()


@pytest.mark.asyncio
async def test_jsonl_writer_read_all_round_trips_persisted_logs(tmp_path: Path) -> None:
    path = tmp_path / "voice.jsonl"
    writer = JSONLVoiceLogWriter(path)
    log_a = _build_log(turn_index=0, eou_at=_now())
    log_b = _build_log(turn_index=1, transport_in_ms=15.5)
    await writer.write(log_a)
    await writer.write(log_b)
    replayed = writer.read_all()
    assert replayed == [log_a, log_b]


def test_jsonl_writer_read_all_on_missing_file_is_empty(tmp_path: Path) -> None:
    """An agent worker that never wrote a turn (e.g. immediate abrupt
    disconnect) should not raise — the audit replay degrades gracefully."""
    writer = JSONLVoiceLogWriter(tmp_path / "never_written.jsonl")
    assert writer.read_all() == []


@pytest.mark.asyncio
async def test_jsonl_writer_skips_blank_lines_on_replay(tmp_path: Path) -> None:
    """Defensive: corrupted JSONL with stray newlines (e.g. fsck recovery)
    must not break replay — skip blank lines, surface only well-formed entries."""
    path = tmp_path / "voice.jsonl"
    writer = JSONLVoiceLogWriter(path)
    await writer.write(_build_log(turn_index=0))
    # Inject a blank line that a buggy editor or recovery tool might leave.
    with path.open("a", encoding="utf-8") as fp:
        fp.write("\n")
    await writer.write(_build_log(turn_index=1))
    replayed = writer.read_all()
    assert [log.turn_index for log in replayed] == [0, 1]


# ---------- Spec V2 T08 additive STT fields ---------------------------------


def test_voice_log_stt_partial_first_at_round_trips() -> None:
    """T08 + D-V2-X-cost-discipline + D-05-9 — Jarvis-feel partial-onset anchor."""
    ts = datetime.now(UTC)
    log = _build_log(stt_partial_first_at=ts)
    json_str = log.model_dump_json()
    rehydrated = VoiceLog.model_validate_json(json_str)
    assert rehydrated.stt_partial_first_at == ts


def test_voice_log_stt_audio_pushed_at_round_trips() -> None:
    ts = datetime.now(UTC)
    log = _build_log(stt_audio_pushed_at=ts)
    json_str = log.model_dump_json()
    rehydrated = VoiceLog.model_validate_json(json_str)
    assert rehydrated.stt_audio_pushed_at == ts


def test_voice_log_stt_provider_cost_field_rejects_negative() -> None:
    """ge=0.0 constraint validates."""
    with pytest.raises(ValidationError):
        _build_log(stt_provider_cost_cents_per_minute=-0.01)


def test_voice_log_stt_total_cents_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        _build_log(stt_total_cents=-1.0)


def test_voice_log_all_t08_fields_default_to_none() -> None:
    """Backwards-compatible — V1's existing 18 tests pass byte-for-byte."""
    log = _build_log()
    assert log.stt_partial_first_at is None
    assert log.stt_audio_pushed_at is None
    assert log.stt_provider_cost_cents_per_minute is None
    assert log.stt_total_cents is None


def test_voice_log_all_t08_fields_populated_round_trip() -> None:
    """End-to-end JSON round-trip with all 4 T08 fields populated."""
    ts = datetime.now(UTC)
    log = _build_log(
        stt_partial_first_at=ts,
        stt_audio_pushed_at=ts,
        stt_provider_cost_cents_per_minute=0.48,  # Deepgram PAYG cents/min
        stt_total_cents=2.4,  # 5-min call at 0.48 cents/min
    )
    json_str = log.model_dump_json()
    rehydrated = VoiceLog.model_validate_json(json_str)
    assert rehydrated.stt_partial_first_at == ts
    assert rehydrated.stt_audio_pushed_at == ts
    assert rehydrated.stt_provider_cost_cents_per_minute == 0.48
    assert rehydrated.stt_total_cents == 2.4
