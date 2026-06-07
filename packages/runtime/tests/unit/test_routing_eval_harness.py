"""Unit tests for the Spec 18 routing-eval harness (T13).

Exercises both deliverables: :mod:`tools.routing_eval.replay` (load YAML +
build registry + assert expected_tier matches) and
:mod:`tools.routing_eval.aggregate` (read TurnLog JSONL + print histograms).
The N=10 starter fixture is exercised in its entirety here — every entry
must route as labelled or the eval harness is wrong, the router is wrong,
or the labels are wrong (all surface loud).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.routing_eval.aggregate import (
    AggregateStats,
    aggregate_jsonl_paths,
    format_report,
)
from tools.routing_eval.replay import build_eval_registry, load_fixture, replay_fixture

_FIXTURE_PATH = (
    Path(__file__).parents[4] / "tools" / "routing_eval" / "fixtures" / "representative_turns.yaml"
)


# ----- Replay harness -------------------------------------------------------


class TestLoadFixture:
    def test_fixture_loads(self) -> None:
        entries = load_fixture(_FIXTURE_PATH)
        # N=10 starter set per D-18-X-routing-eval-shape.
        assert len(entries) == 10

    def test_every_entry_has_required_fields(self) -> None:
        entries = load_fixture(_FIXTURE_PATH)
        for entry in entries:
            assert entry.description
            assert entry.expected_tier in {"frontier", "mid", "small"}
            assert entry.context is not None


class TestReplayMatchesLabels:
    """**Load-bearing regression test.** Every fixture entry must route as labelled.

    A failure means one of three things — all surface loud:

    1. The router's behaviour changed (regression).
    2. The metadata defaults in :func:`build_eval_registry` drifted from the
       fixture's preamble.
    3. The label is wrong (and needs updating with rationale in the
       ``notes`` field of the YAML entry).
    """

    def test_every_fixture_entry_routes_as_expected(self) -> None:
        failures: list[str] = []
        total = 0
        for entry, chosen, ok in replay_fixture(_FIXTURE_PATH):
            total += 1
            if not ok:
                failures.append(
                    f"{entry.description!r}: expected={entry.expected_tier!r} "
                    f"chosen={chosen!r}\n    notes: {entry.notes}"
                )
        assert not failures, f"{len(failures)}/{total} fixture entries mismatched:\n" + "\n".join(
            failures
        )


class TestBuildEvalRegistry:
    def test_registry_has_three_tiers(self) -> None:
        registry = build_eval_registry()
        assert set(registry.configured_tier_names) == {"frontier", "mid", "small"}

    def test_only_frontier_supports_vision(self) -> None:
        registry = build_eval_registry()
        assert registry.supports_vision_for("frontier") is True
        assert registry.supports_vision_for("mid") is False
        assert registry.supports_vision_for("small") is False

    def test_small_has_shortest_context_window(self) -> None:
        registry = build_eval_registry()
        assert registry.metadata_for("small").context_window == 8_000  # type: ignore[union-attr]
        assert registry.metadata_for("mid").context_window == 200_000  # type: ignore[union-attr]
        assert registry.metadata_for("frontier").context_window == 200_000  # type: ignore[union-attr]


# ----- Aggregate harness ---------------------------------------------------


def _turn_log_line(
    *,
    tier_used: str = "mid",
    fallback_triggered: bool = False,
    fallback_reason: str | None = None,
    routing_latency_ms: float = 1.5,
    cost_cents: float = 0.05,
) -> str:
    payload: dict[str, object] = {
        "conversation_id": "c1",
        "turn_index": 0,
        "tier_used": tier_used,
        "model_name": "m",
        "provider": "anthropic",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "latency_ms": 1000.0,
        "cost_cents": cost_cents,
        "tool_calls": 0,
        "skill_used": None,
        "history_compacted": False,
        "timestamp": datetime.now(UTC).isoformat(),
        "routing_decision": {
            "tier": tier_used,
            "model": "m",
            "rationale": "test",
            "candidates_considered": ["frontier", "mid", "small"],
            "layer1_filter_reasons": {},
            "layer2_score": 0.5,
            "fallback_triggered": fallback_triggered,
            "fallback_reason": fallback_reason,
        },
        "routing_latency_ms": routing_latency_ms,
        "routing_fallback_triggered": fallback_triggered,
        "routing_fallback_reason": fallback_reason,
    }
    return json.dumps(payload)


class TestAggregateEmpty:
    def test_no_files_returns_zeroed_stats(self) -> None:
        stats = aggregate_jsonl_paths([])
        assert stats.total_decisions == 0
        assert stats.fallback_rate == 0.0

    def test_format_report_when_empty(self) -> None:
        stats = AggregateStats()
        assert format_report(stats) == "No TurnLog records found."


class TestAggregateBasic:
    def test_counts_tiers(self, tmp_path: Path) -> None:
        log_path = tmp_path / "c1.jsonl"
        log_path.write_text(
            "\n".join(
                [
                    _turn_log_line(tier_used="frontier"),
                    _turn_log_line(tier_used="mid"),
                    _turn_log_line(tier_used="mid"),
                    _turn_log_line(tier_used="small"),
                ]
            ),
            encoding="utf-8",
        )
        stats = aggregate_jsonl_paths([log_path])
        assert stats.total_decisions == 4
        assert stats.tier_counts["mid"] == 2
        assert stats.tier_counts["frontier"] == 1
        assert stats.tier_counts["small"] == 1

    def test_counts_fallbacks(self, tmp_path: Path) -> None:
        log_path = tmp_path / "c1.jsonl"
        log_path.write_text(
            "\n".join(
                [
                    _turn_log_line(),
                    _turn_log_line(fallback_triggered=True, fallback_reason="timeout"),
                    _turn_log_line(fallback_triggered=True, fallback_reason="scoring_error"),
                    _turn_log_line(),
                ]
            ),
            encoding="utf-8",
        )
        stats = aggregate_jsonl_paths([log_path])
        assert stats.fallback_count == 2
        assert stats.fallback_by_reason["timeout"] == 1
        assert stats.fallback_by_reason["scoring_error"] == 1
        assert stats.fallback_rate == pytest.approx(0.5)

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        log_path = tmp_path / "c1.jsonl"
        log_path.write_text(
            "\n".join(["{not valid json", _turn_log_line(), ""]),
            encoding="utf-8",
        )
        stats = aggregate_jsonl_paths([log_path])
        # 1 valid line counted; 1 malformed skipped silently.
        assert stats.total_decisions == 1

    def test_missing_file_silently_skipped(self, tmp_path: Path) -> None:
        stats = aggregate_jsonl_paths([tmp_path / "nonexistent.jsonl"])
        assert stats.total_decisions == 0


class TestFormatReport:
    def test_report_includes_tier_distribution_and_fallback_band(self, tmp_path: Path) -> None:
        log_path = tmp_path / "c1.jsonl"
        lines = [_turn_log_line(tier_used="mid") for _ in range(20)]
        lines += [_turn_log_line(fallback_triggered=True, fallback_reason="timeout")]
        log_path.write_text("\n".join(lines), encoding="utf-8")
        stats = aggregate_jsonl_paths([log_path])
        report = format_report(stats)
        assert "Total routing decisions: 21" in report
        assert "Tier distribution" in report
        assert "Fallback rate" in report
        # 1/21 ≈ 4.76% → watch band.
        assert "[watch]" in report or "[healthy]" in report
        assert "timeout" in report
        assert "p50=" in report
        assert "p95=" in report
