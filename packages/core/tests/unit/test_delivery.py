"""Tests for ``persona.delivery`` — the channel-delivery boundary (Spec C0, T2).

The ``MessageDeliverer`` Protocol is the one-boundary-many-deliverers port
(D-C0-1 / R-C0-1): the web app implements it now, C1's connectors implement it
next, and direction 4 drives *this same* boundary. These tests pin the minimal
port shape and prove it is satisfiable by a fake deliverer (the contract the web
deliverer + C1 will meet), plus the ``DeliveryOutcome`` / ``DeliveryResult`` value
types routing records (D-C0-5).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.delivery import DeliveryOutcome, DeliveryResult, MessageDeliverer
from persona.schema.origination import OriginatedMessage, PersonaIdentityTag
from pydantic import ValidationError

UTC_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


def _message() -> OriginatedMessage:
    return OriginatedMessage(
        persona=PersonaIdentityTag(persona_id="astrid", display_name="Astrid"),
        owner_user_id="user_1",
        content="I've finished.",
        created_at=UTC_NOW,
    )


class TestDeliveryOutcome:
    def test_has_exactly_the_three_v1_outcomes(self) -> None:
        assert {o.value for o in DeliveryOutcome} == {"delivered", "pending", "failed"}

    def test_is_a_string_enum(self) -> None:
        assert DeliveryOutcome.DELIVERED == "delivered"


class TestDeliveryResult:
    def test_minimal_result_defaults(self) -> None:
        result = DeliveryResult(outcome=DeliveryOutcome.PENDING)
        assert result.outcome is DeliveryOutcome.PENDING
        assert result.channel is None
        assert result.detail is None

    def test_full_result(self) -> None:
        result = DeliveryResult(
            outcome=DeliveryOutcome.DELIVERED, channel="web", detail="open session"
        )
        assert result.outcome is DeliveryOutcome.DELIVERED
        assert result.channel == "web"
        assert result.detail == "open session"

    def test_is_frozen(self) -> None:
        result = DeliveryResult(outcome=DeliveryOutcome.FAILED)
        with pytest.raises(ValidationError):
            result.outcome = DeliveryOutcome.DELIVERED  # type: ignore[misc]

    def test_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            DeliveryResult(outcome=DeliveryOutcome.DELIVERED, channel_name="web")  # type: ignore[call-arg]


class _FakeDeliverer:
    """A channel deliverer satisfying the port structurally (no inheritance)."""

    def __init__(self, outcome: DeliveryOutcome) -> None:
        self._outcome = outcome
        self.delivered: list[OriginatedMessage] = []

    async def deliver(self, message: OriginatedMessage) -> DeliveryResult:
        self.delivered.append(message)
        return DeliveryResult(outcome=self._outcome, channel="fake")


class _NotADeliverer:
    """Lacks ``deliver`` — must NOT satisfy the Protocol."""

    async def send(self, message: OriginatedMessage) -> None: ...


class TestMessageDelivererProtocol:
    def test_fake_deliverer_satisfies_the_runtime_checkable_port(self) -> None:
        deliverer = _FakeDeliverer(DeliveryOutcome.DELIVERED)
        assert isinstance(deliverer, MessageDeliverer)

    def test_object_without_deliver_does_not_satisfy_the_port(self) -> None:
        assert not isinstance(_NotADeliverer(), MessageDeliverer)

    @pytest.mark.asyncio
    async def test_deliver_returns_a_delivery_result(self) -> None:
        deliverer = _FakeDeliverer(DeliveryOutcome.DELIVERED)
        message = _message()
        result = await deliverer.deliver(message)
        assert isinstance(result, DeliveryResult)
        assert result.outcome is DeliveryOutcome.DELIVERED
        assert result.channel == "fake"
        assert deliverer.delivered == [message]

    @pytest.mark.asyncio
    async def test_deliver_can_report_pending_without_raising(self) -> None:
        """Ordinary unreachability is a reported outcome, not a raise (criterion 6)."""
        deliverer = _FakeDeliverer(DeliveryOutcome.PENDING)
        result = await deliverer.deliver(_message())
        assert result.outcome is DeliveryOutcome.PENDING
