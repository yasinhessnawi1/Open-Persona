"""Unit tests for the web-app deliverer (Spec C0, T5).

Pins the two delivery paths of the first ``MessageDeliverer`` (criterion 3) under
the no-push-broker discipline (D-C0-X-no-push-broker): inline on an open live
stream → ``delivered``; no open session → ``pending`` (never dropped, D-C0-4) +
audited (D-C0-5). Collaborators are faked; the real audit row is covered by the
integration test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.delivery import DeliveryOutcome, DeliveryResult, MessageDeliverer
from persona.schema.origination import OriginatedMessage, PersonaIdentityTag
from persona_api.services.web_deliverer import (
    LiveSessionRegistry,
    LiveSessionSink,
    WebAppDeliverer,
)

UTC_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


def _message(*, conversation_id: str | None = "conv_1") -> OriginatedMessage:
    return OriginatedMessage(
        persona=PersonaIdentityTag(persona_id="astrid", display_name="Astrid"),
        owner_user_id="user_1",
        content="I've finished.",
        conversation_id=conversation_id,
        created_at=UTC_NOW,
    )


class _FakeSink:
    def __init__(self) -> None:
        self.pushed: list[OriginatedMessage] = []

    async def push(self, message: OriginatedMessage) -> None:
        self.pushed.append(message)


class _FakeRegistry:
    def __init__(self, sink: _FakeSink | None) -> None:
        self._sink = sink
        self.looked_up: list[OriginatedMessage] = []

    def lookup(self, message: OriginatedMessage) -> _FakeSink | None:
        self.looked_up.append(message)
        return self._sink


class _RecordSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _deliverer(sink: _FakeSink | None, record: _RecordSpy) -> tuple[WebAppDeliverer, _FakeRegistry]:
    registry = _FakeRegistry(sink)
    deliverer = WebAppDeliverer(rls_engine=object(), sessions=registry, record=record)  # type: ignore[arg-type]
    return deliverer, registry


class TestInlineDelivery:
    @pytest.mark.asyncio
    async def test_open_stream_delivers_inline(self) -> None:
        sink, record = _FakeSink(), _RecordSpy()
        deliverer, _ = _deliverer(sink, record)
        message = _message()

        result = await deliverer.deliver(message)

        assert result.outcome is DeliveryOutcome.DELIVERED
        assert result.channel == "web"
        assert sink.pushed == [message]  # pushed onto the open run stream

    @pytest.mark.asyncio
    async def test_delivered_outcome_is_audited(self) -> None:
        sink, record = _FakeSink(), _RecordSpy()
        deliverer, _ = _deliverer(sink, record)
        await deliverer.deliver(_message())
        assert len(record.calls) == 1
        assert record.calls[0]["action"] == "origination.delivery.delivered"
        assert record.calls[0]["user_id"] == "user_1"
        assert record.calls[0]["target"] == "conv_1"


class TestNoSessionPending:
    @pytest.mark.asyncio
    async def test_no_open_stream_is_pending_not_failed(self) -> None:
        """No drop: undeliverable → pending (already persisted by T4), never failed."""
        record = _RecordSpy()
        deliverer, registry = _deliverer(None, record)
        message = _message()

        result = await deliverer.deliver(message)

        assert result.outcome is DeliveryOutcome.PENDING
        assert result.outcome is not DeliveryOutcome.FAILED
        assert registry.looked_up == [message]  # consulted the registry, found nothing

    @pytest.mark.asyncio
    async def test_pending_outcome_is_audited(self) -> None:
        record = _RecordSpy()
        deliverer, _ = _deliverer(None, record)
        await deliverer.deliver(_message())
        assert len(record.calls) == 1
        assert record.calls[0]["action"] == "origination.delivery.pending"
        assert record.calls[0]["metadata"]["channel"] == "web"

    @pytest.mark.asyncio
    async def test_no_session_does_not_push_anything(self) -> None:
        sink, record = _FakeSink(), _RecordSpy()
        # sink exists but the registry returns None → nothing is pushed
        registry = _FakeRegistry(None)
        deliverer = WebAppDeliverer(rls_engine=object(), sessions=registry, record=record)  # type: ignore[arg-type]
        await deliverer.deliver(_message())
        assert sink.pushed == []


class TestProtocolAndSeams:
    def test_web_deliverer_satisfies_the_message_deliverer_port(self) -> None:
        deliverer, _ = _deliverer(None, _RecordSpy())
        assert isinstance(deliverer, MessageDeliverer)

    def test_fakes_satisfy_the_session_seams(self) -> None:
        assert isinstance(_FakeSink(), LiveSessionSink)
        assert isinstance(_FakeRegistry(None), LiveSessionRegistry)

    @pytest.mark.asyncio
    async def test_returns_a_delivery_result(self) -> None:
        deliverer, _ = _deliverer(None, _RecordSpy())
        assert isinstance(await deliverer.deliver(_message()), DeliveryResult)
