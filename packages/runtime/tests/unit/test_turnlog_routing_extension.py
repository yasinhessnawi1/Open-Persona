"""Unit tests for the Spec 18 TurnLog routing extension (T12).

Verifies the additive D-18-X-turnlog-extension fields are correctly typed,
default sensibly when omitted (pre-Spec-18 callers stay green), and
round-trip via :meth:`TurnLog.model_dump_json` → :meth:`TurnLog.model_validate_json`
with the nested :class:`RoutingDecision` intact (D-05-9 boundary discipline).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from persona_runtime.logging import TurnLog
from persona_runtime.routing import RoutingDecision
from pydantic import ValidationError


def _now() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


def _baseline_log_kwargs() -> dict[str, object]:
    """Minimum required fields for TurnLog — pre-Spec-18 callers use these."""
    return {
        "conversation_id": "c1",
        "turn_index": 0,
        "tier_used": "mid",
        "model_name": "claude-haiku-4-5",
        "provider": "anthropic",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "latency_ms": 1234.5,
        "cost_cents": 0.5,
        "timestamp": _now(),
    }


def _decision(
    *,
    fallback_triggered: bool = False,
    fallback_reason: str | None = None,
) -> RoutingDecision:
    return RoutingDecision(
        tier="mid",
        model="claude-haiku-4-5",
        rationale="layer2: best=mid score=0.612",
        candidates_considered=("frontier", "mid", "small"),
        layer1_filter_reasons={},
        layer2_score=0.612,
        fallback_triggered=fallback_triggered,
        fallback_reason=fallback_reason,
    )


class TestRoutingFieldsDefault:
    def test_legacy_construction_without_routing_fields_succeeds(self) -> None:
        """Pre-Spec-18 callers (existing test_loop.py + test_logging.py) stay green."""
        log = TurnLog(**_baseline_log_kwargs())  # type: ignore[arg-type]
        assert log.routing_decision is None
        assert log.routing_latency_ms == 0.0
        assert log.routing_fallback_triggered is False
        assert log.routing_fallback_reason is None


class TestRoutingFieldsPopulated:
    def test_construction_with_routing_decision(self) -> None:
        decision = _decision()
        log = TurnLog(
            **_baseline_log_kwargs(),  # type: ignore[arg-type]
            routing_decision=decision,
            routing_latency_ms=2.5,
        )
        assert log.routing_decision is decision
        assert log.routing_latency_ms == pytest.approx(2.5)
        assert log.routing_fallback_triggered is False
        assert log.routing_fallback_reason is None

    def test_fallback_fields_recorded(self) -> None:
        decision = _decision(fallback_triggered=True, fallback_reason="timeout")
        log = TurnLog(
            **_baseline_log_kwargs(),  # type: ignore[arg-type]
            routing_decision=decision,
            routing_latency_ms=120.0,
            routing_fallback_triggered=True,
            routing_fallback_reason="timeout",
        )
        assert log.routing_fallback_triggered is True
        assert log.routing_fallback_reason == "timeout"


class TestRoutingFieldsValidation:
    def test_negative_routing_latency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TurnLog(
                **_baseline_log_kwargs(),  # type: ignore[arg-type]
                routing_latency_ms=-1.0,
            )

    def test_extra_routing_field_rejected(self) -> None:
        # extra=forbid still applies — adding a rogue field fails.
        with pytest.raises(ValidationError):
            TurnLog(
                **_baseline_log_kwargs(),  # type: ignore[arg-type]
                routing_undeclared="rogue",  # type: ignore[call-arg]
            )


class TestJsonRoundTrip:
    """D-05-9 boundary discipline — nested RoutingDecision serialises through Postgres JSONB."""

    def test_round_trip_with_routing_decision(self) -> None:
        decision = _decision()
        original = TurnLog(
            **_baseline_log_kwargs(),  # type: ignore[arg-type]
            routing_decision=decision,
            routing_latency_ms=3.2,
            routing_fallback_triggered=False,
            routing_fallback_reason=None,
        )
        as_json = original.model_dump_json()
        restored = TurnLog.model_validate_json(as_json)
        assert restored == original
        assert restored.routing_decision == decision

    def test_round_trip_with_fallback_recorded(self) -> None:
        decision = _decision(
            fallback_triggered=True,
            fallback_reason="partial_metadata:small",
        )
        original = TurnLog(
            **_baseline_log_kwargs(),  # type: ignore[arg-type]
            routing_decision=decision,
            routing_latency_ms=15.0,
            routing_fallback_triggered=True,
            routing_fallback_reason="partial_metadata:small",
        )
        as_json = original.model_dump_json()
        parsed = json.loads(as_json)
        assert parsed["routing_fallback_triggered"] is True
        assert parsed["routing_fallback_reason"] == "partial_metadata:small"
        # Nested RoutingDecision JSON serialises cleanly.
        assert parsed["routing_decision"]["tier"] == "mid"
        assert parsed["routing_decision"]["candidates_considered"] == ["frontier", "mid", "small"]
        restored = TurnLog.model_validate_json(as_json)
        assert restored == original

    def test_legacy_log_round_trip_when_routing_decision_omitted(self) -> None:
        original = TurnLog(**_baseline_log_kwargs())  # type: ignore[arg-type]
        as_json = original.model_dump_json()
        restored = TurnLog.model_validate_json(as_json)
        assert restored == original
        assert restored.routing_decision is None
