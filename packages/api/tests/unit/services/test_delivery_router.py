"""Unit tests for the delivery router (Spec C0, T6, D-C0-2).

Pins the v1 routing guarantees: ONE target per message (no double-delivery by
construction), the web home as the always-available fallback (no silent drop),
no platform branching (a registry-key lookup via an injected resolver), and the
routing decision + outcome tracked via the audit log (D-C0-5).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.delivery import DeliveryOutcome, DeliveryResult, MessageDeliverer
from persona.schema.origination import OriginatedMessage, PersonaIdentityTag
from persona_api.services.delivery_router import DeliveryRouter

UTC_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


def _message(*, conversation_id: str | None = "conv_1") -> OriginatedMessage:
    return OriginatedMessage(
        persona=PersonaIdentityTag(persona_id="astrid", display_name="Astrid"),
        owner_user_id="user_1",
        content="done",
        conversation_id=conversation_id,
        created_at=UTC_NOW,
    )


class _FakeDeliverer:
    def __init__(self, channel: str, outcome: DeliveryOutcome = DeliveryOutcome.DELIVERED) -> None:
        self._channel = channel
        self._outcome = outcome
        self.delivered: list[OriginatedMessage] = []

    async def deliver(self, message: OriginatedMessage) -> DeliveryResult:
        self.delivered.append(message)
        return DeliveryResult(outcome=self._outcome, channel=self._channel)


class _RecordSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _router(
    deliverers: dict[str, _FakeDeliverer],
    *,
    resolve: object = None,
    record: _RecordSpy | None = None,
) -> DeliveryRouter:
    kwargs: dict[str, object] = {
        "deliverers": deliverers,
        "rls_engine": object(),
        "record": record or _RecordSpy(),
    }
    if resolve is not None:
        kwargs["resolve_channel"] = resolve
    return DeliveryRouter(**kwargs)  # type: ignore[arg-type]


class TestOneTargetNoDoubleDelivery:
    @pytest.mark.asyncio
    async def test_routes_to_exactly_one_deliverer(self) -> None:
        web = _FakeDeliverer("web")
        telegram = _FakeDeliverer("telegram")
        router = _router(
            {"web": web, "telegram": telegram},
            resolve=lambda _m: "telegram",
        )

        result = await router.deliver(_message())

        assert result.channel == "telegram"
        assert len(telegram.delivered) == 1
        assert web.delivered == []  # the other channel is NEVER also called

    @pytest.mark.asyncio
    async def test_resolver_none_routes_to_home_only(self) -> None:
        web = _FakeDeliverer("web")
        telegram = _FakeDeliverer("telegram")
        router = _router({"web": web, "telegram": telegram})  # default resolver → home

        await router.deliver(_message())

        assert len(web.delivered) == 1
        assert telegram.delivered == []


class TestNoSilentDrop:
    @pytest.mark.asyncio
    async def test_unknown_channel_falls_back_to_home(self) -> None:
        web = _FakeDeliverer("web")
        router = _router({"web": web}, resolve=lambda _m: "nonexistent")
        result = await router.deliver(_message())
        assert len(web.delivered) == 1  # fell back to the home channel, not dropped
        assert result.channel == "web"

    def test_home_channel_must_be_registered_fail_fast(self) -> None:
        with pytest.raises(ValueError, match="home_channel"):
            _router({"telegram": _FakeDeliverer("telegram")})  # no 'web' home

    @pytest.mark.asyncio
    async def test_pending_outcome_is_not_a_drop(self) -> None:
        web = _FakeDeliverer("web", DeliveryOutcome.PENDING)
        result = await _router({"web": web}).deliver(_message())
        assert result.outcome is DeliveryOutcome.PENDING  # present-on-next-open, not lost


class TestOutcomeTracked:
    @pytest.mark.asyncio
    async def test_routing_decision_and_outcome_audited(self) -> None:
        record = _RecordSpy()
        web = _FakeDeliverer("web", DeliveryOutcome.PENDING)
        router = _router({"web": web}, record=record)

        await router.deliver(_message())

        assert len(record.calls) == 1
        call = record.calls[0]
        assert call["action"] == "origination.routing"
        assert call["target"] == "conv_1"
        assert call["metadata"]["channel"] == "web"  # type: ignore[index]
        assert call["metadata"]["outcome"] == "pending"  # type: ignore[index]


class TestProtocol:
    def test_router_is_itself_a_message_deliverer(self) -> None:
        router = _router({"web": _FakeDeliverer("web")})
        assert isinstance(router, MessageDeliverer)

    @pytest.mark.asyncio
    async def test_returns_chosen_deliverers_result_verbatim(self) -> None:
        web = _FakeDeliverer("web", DeliveryOutcome.DELIVERED)
        result = await _router({"web": web}).deliver(_message())
        assert result.outcome is DeliveryOutcome.DELIVERED
        assert result.channel == "web"
