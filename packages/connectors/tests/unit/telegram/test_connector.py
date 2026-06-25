"""TelegramConnector — C1 Connector + C0 MessageDeliverer (Spec C2 T5).

Unit-level (offline): the real client over ``httpx.MockTransport`` proves the
send chain (render → split → Bot API), a fake store + recording owner-scope prove
the GAP-A deliver bridge (resolve_channel → assemble → send), and protocol
isinstance checks prove the adapter satisfies both seams.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest
from persona.delivery import DeliveryOutcome, MessageDeliverer
from persona.schema.origination import OriginatedMessage, PersonaIdentityTag
from persona_connectors.domain.conversation_model import ChannelRef
from persona_connectors.domain.normalise import NormalisedOutbound
from persona_connectors.domain.protocol import Connector
from persona_connectors.telegram.client import TelegramClient
from persona_connectors.telegram.connector import TelegramConnector
from pydantic import SecretStr

if TYPE_CHECKING:
    from collections.abc import Iterator

_TOKEN = "123456:SECRET"  # noqa: S105 — test literal
_PERSONA = PersonaIdentityTag(persona_id="pa", display_name="Astrid", visual_ref=None)


def _client(handler: object) -> TelegramClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return TelegramClient(bot_token=SecretStr(_TOKEN), http=httpx.AsyncClient(transport=transport))


class _FakeStore:
    """A minimal ConversationStateStore stand-in exposing only resolve_channel."""

    def __init__(self, ref: ChannelRef | None) -> None:
        self._ref = ref
        self.seen_conversation_id: str | None = None

    def resolve_channel(self, *, conversation_id: str) -> ChannelRef | None:
        self.seen_conversation_id = conversation_id
        return self._ref


def _scope_recorder(entered: list[str]) -> object:
    @contextlib.contextmanager
    def scope(owner_id: str) -> Iterator[None]:
        entered.append(owner_id)
        yield

    return scope


def _connector(
    handler: object, *, ref: ChannelRef | None = None, entered: list[str] | None = None
) -> TelegramConnector:
    return TelegramConnector(
        client=_client(handler),
        conversation_store=_FakeStore(ref),  # type: ignore[arg-type]
        owner_scope=_scope_recorder(entered if entered is not None else []),  # type: ignore[arg-type]
    )


def _originated(conversation_id: str | None) -> OriginatedMessage:
    return OriginatedMessage(
        persona=_PERSONA,
        owner_user_id="user_a",
        content="I've finished the task.",
        conversation_id=conversation_id,
        created_at=datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC),
    )


# --- protocol conformance ---


def test_connector_satisfies_both_protocols() -> None:
    """It IS a C1 Connector AND a C0 MessageDeliverer (registers into the DeliveryRouter)."""
    connector = _connector(lambda _r: httpx.Response(200, json={"ok": True, "result": {}}))
    assert isinstance(connector, Connector)
    assert isinstance(connector, MessageDeliverer)
    assert connector.platform == "telegram"
    assert connector.capabilities.supports_rich_formatting is True


# --- send (Connector) ---


@pytest.mark.asyncio
async def test_send_renders_bold_html_and_reports_delivered() -> None:
    """send lowers the name tag to HTML bold + reports DELIVERED."""
    sent: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    connector = _connector(handler)
    outbound = NormalisedOutbound(persona=_PERSONA, text="hello", conversation_key="555")
    result = await connector.send(outbound)

    assert result.outcome == DeliveryOutcome.DELIVERED
    assert result.channel == "telegram"
    assert sent["body"] == {
        "chat_id": "555",
        "text": "<b>Astrid</b>\nhello",
        "parse_mode": "HTML",
    }


@pytest.mark.asyncio
async def test_send_rate_limited_reports_pending_not_a_raise() -> None:
    """A 429 → PENDING (retryable), never a silent drop (D-C1-X-platform-rejection)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 5}}
        )

    connector = _connector(handler)
    result = await connector.send(
        NormalisedOutbound(persona=_PERSONA, text="x", conversation_key="1")
    )
    assert result.outcome == DeliveryOutcome.PENDING
    assert _TOKEN not in (result.detail or "")


@pytest.mark.asyncio
async def test_send_rejection_reports_failed() -> None:
    """A non-retryable rejection → FAILED + a safe detail (no token)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "blocked"})

    connector = _connector(handler)
    result = await connector.send(
        NormalisedOutbound(persona=_PERSONA, text="x", conversation_key="1")
    )
    assert result.outcome == DeliveryOutcome.FAILED
    assert _TOKEN not in (result.detail or "")


# --- deliver (MessageDeliverer / GAP-A bridge) ---


@pytest.mark.asyncio
async def test_deliver_resolves_channel_then_sends() -> None:
    """deliver: owner-scope → resolve_channel → assemble NormalisedOutbound → send."""
    sent: dict[str, object] = {}
    entered: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    connector = _connector(
        handler, ref=ChannelRef(platform="telegram", channel_key="999"), entered=entered
    )
    result = await connector.deliver(_originated("conv_abc"))

    assert result.outcome == DeliveryOutcome.DELIVERED
    # Owner scope was entered with the originated message's owner BEFORE the lookup.
    assert entered == ["user_a"]
    # The resolved channel_key became the Telegram chat_id; the name tag rendered.
    assert sent["body"] == {
        "chat_id": "999",
        "text": "<b>Astrid</b>\nI've finished the task.",
        "parse_mode": "HTML",
    }


@pytest.mark.asyncio
async def test_deliver_no_conversation_is_pending() -> None:
    """An originated message with no conversation_id → PENDING (never lost)."""
    connector = _connector(lambda _r: httpx.Response(200, json={"ok": True, "result": {}}))
    result = await connector.deliver(_originated(None))
    assert result.outcome == DeliveryOutcome.PENDING


@pytest.mark.asyncio
async def test_deliver_unresolved_channel_is_pending() -> None:
    """resolve_channel → None (e.g. a web-only conversation) → PENDING, no send."""
    connector = _connector(
        lambda _r: httpx.Response(200, json={"ok": True, "result": {}}), ref=None
    )
    result = await connector.deliver(_originated("conv_web_only"))
    assert result.outcome == DeliveryOutcome.PENDING
    assert result.detail == "no connector channel for conversation"


@pytest.mark.asyncio
async def test_deliver_other_platform_channel_is_pending() -> None:
    """Defensive: a non-telegram ChannelRef is not ours → PENDING (don't mis-send)."""
    connector = _connector(
        lambda _r: httpx.Response(200, json={"ok": True, "result": {}}),
        ref=ChannelRef(platform="discord", channel_key="d1"),
    )
    result = await connector.deliver(_originated("conv_discord"))
    assert result.outcome == DeliveryOutcome.PENDING
