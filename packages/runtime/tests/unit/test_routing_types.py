"""Unit tests for the Spec 18 routing boundary types (T02).

Covers construction, frozen-ness, ``extra="forbid"`` rejection, field
validation, JSON round-trip (the D-05-9 boundary discipline TurnLog inherits
in T12), and the ``RoutingProfile`` literal extensibility surface. Plus a
small audit-confirmation test against :class:`TurnLog` so the T12 additive
extension's Pydantic-frozen + ``extra="forbid"`` precondition is regression
guarded.
"""

from __future__ import annotations

import json

import pytest
from persona_runtime.logging import TurnLog
from persona_runtime.routing import RoutingContext, RoutingDecision
from pydantic import ValidationError

# ----- RoutingContext -------------------------------------------------------


class TestRoutingContextConstruction:
    def test_construction_with_all_fields(self) -> None:
        ctx = RoutingContext(
            requires_vision=False,
            estimated_input_tokens=200,
            requires_strong_tools=False,
            is_first_turn=True,
            is_identity_sensitive=False,
            conversation_phase="opening",
            profile="text_default",
        )
        assert ctx.profile == "text_default"
        assert ctx.estimated_input_tokens == 200
        assert ctx.is_first_turn is True

    def test_voice_profile_accepted(self) -> None:
        ctx = RoutingContext(
            requires_vision=False,
            estimated_input_tokens=80,
            requires_strong_tools=False,
            is_first_turn=False,
            is_identity_sensitive=False,
            conversation_phase="middle",
            profile="voice",
        )
        assert ctx.profile == "voice"


class TestRoutingContextFrozen:
    def test_attribute_assignment_raises(self) -> None:
        ctx = RoutingContext(
            requires_vision=False,
            estimated_input_tokens=100,
            requires_strong_tools=False,
            is_first_turn=False,
            is_identity_sensitive=False,
            conversation_phase="middle",
            profile="text_default",
        )
        with pytest.raises(ValidationError):
            ctx.profile = "voice"  # type: ignore[misc]


class TestRoutingContextExtraForbid:
    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            RoutingContext(
                requires_vision=False,
                estimated_input_tokens=100,
                requires_strong_tools=False,
                is_first_turn=False,
                is_identity_sensitive=False,
                conversation_phase="middle",
                profile="text_default",
                undeclared_field="rogue",  # type: ignore[call-arg]
            )
        # Pydantic v2 surfaces ``extra_forbidden`` as the error type; assert on
        # the rendered message rather than couple to internal type identifiers.
        message = str(excinfo.value).lower()
        assert "extra" in message or "forbid" in message


class TestRoutingContextValidation:
    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingContext(
                requires_vision=False,
                estimated_input_tokens=-1,
                requires_strong_tools=False,
                is_first_turn=False,
                is_identity_sensitive=False,
                conversation_phase="middle",
                profile="text_default",
            )

    def test_invalid_profile_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingContext(
                requires_vision=False,
                estimated_input_tokens=100,
                requires_strong_tools=False,
                is_first_turn=False,
                is_identity_sensitive=False,
                conversation_phase="middle",
                profile="agentic",  # type: ignore[arg-type]
            )

    def test_zero_tokens_accepted(self) -> None:
        # Edge — empty user message is plausible for some signals.
        ctx = RoutingContext(
            requires_vision=False,
            estimated_input_tokens=0,
            requires_strong_tools=False,
            is_first_turn=False,
            is_identity_sensitive=False,
            conversation_phase="middle",
            profile="text_default",
        )
        assert ctx.estimated_input_tokens == 0


# ----- RoutingDecision ------------------------------------------------------


class TestRoutingDecisionConstruction:
    def test_construction_minimal(self) -> None:
        dec = RoutingDecision(
            tier="frontier",
            model="claude-sonnet-4-6",
            rationale="first turn → frontier",
            candidates_considered=("frontier", "mid", "small"),
        )
        assert dec.tier == "frontier"
        assert dec.layer2_score == 0.0
        assert dec.layer1_filter_reasons == {}
        assert dec.candidates_considered == ("frontier", "mid", "small")

    def test_construction_with_layer1_reasons(self) -> None:
        dec = RoutingDecision(
            tier="frontier",
            model="claude-opus-4-7",
            rationale="vision required; layer1 filtered out small",
            candidates_considered=("frontier", "mid"),
            layer1_filter_reasons={"small": "no_vision_capability"},
            layer2_score=0.82,
        )
        assert dec.layer1_filter_reasons == {"small": "no_vision_capability"}
        assert dec.layer2_score == pytest.approx(0.82)


class TestRoutingDecisionFrozen:
    def test_attribute_assignment_raises(self) -> None:
        dec = RoutingDecision(
            tier="mid",
            model="claude-haiku-4-5",
            rationale="default mid",
            candidates_considered=("mid",),
        )
        with pytest.raises(ValidationError):
            dec.tier = "frontier"  # type: ignore[misc]


class TestRoutingDecisionExtraForbid:
    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingDecision(
                tier="mid",
                model="claude-haiku-4-5",
                rationale="default mid",
                candidates_considered=("mid",),
                undeclared="rogue",  # type: ignore[call-arg]
            )


class TestRoutingDecisionJsonRoundTrip:
    """D-05-9 boundary discipline — TurnLog serialises RoutingDecision via Postgres JSONB.

    Round-trip must preserve every field. Pydantic v2 emits the
    ``tuple[str, ...]`` as a JSON array and parses it back as a tuple via the
    type adapter — verified here so T12's additive TurnLog extension can rely
    on this without a follow-up shape test.
    """

    def test_round_trip_preserves_all_fields(self) -> None:
        original = RoutingDecision(
            tier="frontier",
            model="claude-opus-4-7",
            rationale="vision required; layer1 filtered out small",
            candidates_considered=("frontier", "mid"),
            layer1_filter_reasons={"small": "no_vision_capability"},
            layer2_score=0.82,
        )
        as_json = original.model_dump_json()
        restored = RoutingDecision.model_validate_json(as_json)
        assert restored == original

    def test_round_trip_minimal_defaults(self) -> None:
        original = RoutingDecision(
            tier="mid",
            model="claude-haiku-4-5",
            rationale="default mid",
            candidates_considered=("mid",),
        )
        as_json = original.model_dump_json()
        parsed = json.loads(as_json)
        assert parsed["layer1_filter_reasons"] == {}
        assert parsed["layer2_score"] == 0.0
        assert parsed["candidates_considered"] == ["mid"]
        restored = RoutingDecision.model_validate_json(as_json)
        assert restored == original


# ----- TurnLog audit confirmation (T02 prerequisite for T12) ----------------


class TestTurnLogPydanticShape:
    """T02 audit confirmation: TurnLog is Pydantic v2 frozen + ``extra="forbid"``.

    D-18-X-turnlog-extension lands additively on this shape (T12). If TurnLog
    drifts to ``@dataclass`` or relaxes its config, T12's extension breaks;
    this test fails loud first. Mirrors the audit recorded in
    ``docs/specs/phase2/spec_18/state.md`` section E.
    """

    def test_turnlog_is_frozen(self) -> None:
        assert TurnLog.model_config.get("frozen") is True

    def test_turnlog_forbids_extra(self) -> None:
        assert TurnLog.model_config.get("extra") == "forbid"
