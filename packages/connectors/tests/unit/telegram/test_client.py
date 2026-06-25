"""TelegramClient — the Bot API transport boundary (Spec C2 T1).

Exercised entirely offline via ``httpx.MockTransport`` (no network): a handler
inspects the request and returns a canned Bot API reply, so the tests assert the
client's request shape + its mapping of replies/faults to domain errors, and —
critically — that the bot token NEVER leaks into an error.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
from persona_connectors.errors import TelegramApiError, TelegramRateLimitError
from persona_connectors.telegram.client import TelegramClient
from pydantic import SecretStr

if TYPE_CHECKING:
    from collections.abc import Callable

_TOKEN = "123456:SUPER-SECRET-BOT-TOKEN"  # noqa: S105 — test literal


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> TelegramClient:
    """Build a TelegramClient whose httpx client routes through a mock handler."""
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return TelegramClient(bot_token=SecretStr(_TOKEN), http=http)


def _ok(result: object) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


@pytest.mark.asyncio
async def test_send_message_posts_expected_shape_and_returns_result() -> None:
    """sendMessage posts chat_id/text/parse_mode and returns the sent Message."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _ok({"message_id": 42, "text": "hi"})

    client = _client(handler)
    result = await client.send_message(chat_id="555", text="hi", parse_mode="HTML")

    assert result == {"message_id": 42, "text": "hi"}
    assert seen["body"] == {"chat_id": "555", "text": "hi", "parse_mode": "HTML"}
    # The method (and the token) are in the URL path — the client builds it correctly.
    assert str(seen["url"]).endswith("/sendMessage")


@pytest.mark.asyncio
async def test_reply_to_message_id_is_threaded_as_int() -> None:
    """reply_to_message_id is sent as an integer (Telegram expects an int)."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok({"message_id": 7})

    client = _client(handler)
    await client.send_message(chat_id="1", text="x", reply_to_message_id="99")
    assert seen["body"] == {"chat_id": "1", "text": "x", "reply_to_message_id": 99}


@pytest.mark.asyncio
async def test_send_chat_action_posts_typing() -> None:
    """sendChatAction posts the typing action (the working cue, D-C2-4)."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok(True)

    client = _client(handler)
    await client.send_chat_action(chat_id="9")
    assert seen["body"] == {"chat_id": "9", "action": "typing"}


@pytest.mark.asyncio
async def test_set_webhook_passes_secret_token_and_allowed_updates() -> None:
    """setWebhook carries the secret token (D-C2-2) + the allowed-updates narrowing."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok(True)

    client = _client(handler)
    await client.set_webhook(
        url="https://example.test/telegram/webhook",
        secret_token="hook-secret",  # noqa: S106 — test literal
        allowed_updates=["message"],
    )
    assert seen["body"] == {
        "url": "https://example.test/telegram/webhook",
        "secret_token": "hook-secret",
        "allowed_updates": ["message"],
    }


@pytest.mark.asyncio
async def test_get_updates_returns_update_list() -> None:
    """getUpdates (the dev long-poll transport) returns the raw Update objects."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"timeout": 30, "offset": 5, "allowed_updates": ["message"]}
        return _ok([{"update_id": 5, "message": {"message_id": 1}}])

    client = _client(handler)
    updates = await client.get_updates(offset=5, timeout=30, allowed_updates=["message"])
    assert updates == [{"update_id": 5, "message": {"message_id": 1}}]


@pytest.mark.asyncio
async def test_get_me_returns_the_bot_user() -> None:
    """getMe returns the bot User (used to validate the token + read the @username)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok({"id": 1, "is_bot": True, "username": "my_persona_bot"})

    client = _client(handler)
    me = await client.get_me()
    assert me["username"] == "my_persona_bot"


@pytest.mark.asyncio
async def test_ok_false_maps_to_telegram_api_error() -> None:
    """A logical rejection (ok: false) becomes a TelegramApiError with safe context."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )

    client = _client(handler)
    with pytest.raises(TelegramApiError) as excinfo:
        await client.send_message(chat_id="nope", text="x")

    err = excinfo.value
    assert err.context == {"method": "sendMessage", "error_code": "400"}
    # Telegram's bot-facing description is safe to surface (no secret).
    assert "chat not found" in str(err)


@pytest.mark.asyncio
async def test_429_maps_to_rate_limit_error_with_retry_after() -> None:
    """A 429 becomes a TelegramRateLimitError carrying retry_after (C2-R-1)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 12",
                "parameters": {"retry_after": 12},
            },
        )

    client = _client(handler)
    with pytest.raises(TelegramRateLimitError) as excinfo:
        await client.send_message(chat_id="1", text="x")
    assert excinfo.value.retry_after == 12


@pytest.mark.asyncio
async def test_network_fault_maps_to_domain_error_without_leaking_token() -> None:
    """A transport fault becomes a TelegramApiError — and the bot token NEVER leaks.

    The token rides in the request URL; the httpx exception carries that URL, so
    the client must suppress it (``from None``). This is the D-C2-X-credential
    guarantee asserted directly.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client(handler)
    with pytest.raises(TelegramApiError) as excinfo:
        await client.send_message(chat_id="1", text="x")

    err = excinfo.value
    assert err.context == {"method": "sendMessage"}
    # The token must not appear in the message, the context, OR any chained cause.
    assert _TOKEN not in str(err)
    assert _TOKEN not in repr(err)
    assert err.__cause__ is None  # httpx exception suppressed (from None)
    assert err.__suppress_context__ is True


@pytest.mark.asyncio
async def test_non_object_response_maps_to_domain_error() -> None:
    """A non-object JSON body is a fault, not a crash."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected"])

    client = _client(handler)
    with pytest.raises(TelegramApiError):
        await client.get_me()
