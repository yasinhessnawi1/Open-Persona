"""The Telegram Bot API client (Spec C2 T1) — the thin transport boundary.

The Bot API is plain JSON-over-HTTPS (``POST {base}/bot<token>/<method>`` with a
JSON body, JSON reply ``{"ok": true, "result": …}`` or ``{"ok": false,
"error_code": …, "description": …}``). So this adapter talks to it with ``httpx``
directly rather than adopting a heavyweight SDK that would invert control and
duplicate C1's flow (D-C2-X-no-new-dep). The client is the **single Telegram I/O
boundary**: every Bot API call the adapter needs (``getMe`` / ``sendMessage`` /
``sendChatAction`` / ``setWebhook`` / ``deleteWebhook`` / ``getUpdates``) goes
through one ``_call`` that maps every transport fault or logical rejection to a
:class:`~persona_connectors.errors.TelegramApiError` (the ENG-STD
catch-at-the-boundary rule) and a ``429`` to a
:class:`~persona_connectors.errors.TelegramRateLimitError` carrying ``retry_after``.

**Credential safety (D-C2-X-credential).** The bot token rides in the request URL
(``/bot<token>/<method>``). It is unwrapped from its :class:`~pydantic.SecretStr`
**only** at the call site and **never** appears in a log line, an exception
message, or an exception ``context``: on an ``httpx`` failure the underlying
exception (which carries the token-bearing URL) is suppressed with ``raise … from
None``, and the raised domain error carries only the method name + status /
``error_code``. Telegram's own ``description`` is bot-facing text (e.g. "Bad
Request: chat not found") and carries no secret, so it is safe to surface.

This module is **api-free** (httpx + persona-core errors only) — it never imports
``persona_api``; it is pure platform I/O composed at the root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from persona_connectors.errors import TelegramApiError, TelegramRateLimitError

if TYPE_CHECKING:
    from pydantic import SecretStr

__all__ = ["TelegramClient"]

# The Bot API documents a per-message text cap of 4096 characters (C2-R-1); the
# splitter (D-C2-3, a later task) budgets against this. Exposed here as the
# single source of the platform fact.
TELEGRAM_MAX_MESSAGE_CHARS = 4096


class TelegramClient:
    """A thin async client over the Telegram Bot API (Spec C2 T1).

    Holds the bot token (a :class:`~pydantic.SecretStr`) and an injected
    :class:`httpx.AsyncClient` (DI — the composition root owns the client's
    timeouts/pool; long-poll needs a read timeout above the poll timeout). No
    globals, no module state.
    """

    def __init__(
        self,
        *,
        bot_token: SecretStr,
        http: httpx.AsyncClient,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        self._token = bot_token
        self._http = http
        self._base = api_base_url.rstrip("/")

    def _url(self, method: str) -> str:
        """Build the method URL. Contains the token — NEVER log or surface this."""
        return f"{self._base}/bot{self._token.get_secret_value()}/{method}"

    async def _call(self, method: str, payload: dict[str, object] | None = None) -> object:
        """POST one Bot API method and return its ``result`` (or raise a domain error).

        Args:
            method: The Bot API method name (e.g. ``"sendMessage"``).
            payload: The JSON request body.

        Returns:
            The decoded ``result`` value from a successful (``ok: true``) reply.

        Raises:
            TelegramRateLimitError: Telegram returned ``429`` — back off ``retry_after``.
            TelegramApiError: Any other transport fault or ``ok: false`` rejection.
        """
        try:
            response = await self._http.post(self._url(method), json=payload or {})
        except httpx.HTTPError:
            # The httpx exception carries the token-bearing request URL — suppress
            # it entirely (``from None``) so the token never reaches a traceback.
            raise TelegramApiError("telegram request failed", context={"method": method}) from None
        return self._parse(method, response)

    @staticmethod
    def _parse(method: str, response: httpx.Response) -> object:
        """Parse a Bot API reply, mapping rejections to domain errors."""
        raw = response.json()
        if not isinstance(raw, dict):
            raise TelegramApiError(
                "telegram returned a non-object response",
                context={"method": method, "status": str(response.status_code)},
            )
        body: dict[str, object] = raw
        if body.get("ok") is True:
            return body.get("result")

        # ok: false — Telegram's ``description`` is bot-facing (no secret); safe.
        description = str(body.get("description", "")).strip()
        error_code = body.get("error_code")
        context = {"method": method, "error_code": str(error_code)}
        parameters = body.get("parameters")
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if retry_after is not None or response.status_code == 429:
            seconds = int(retry_after) if isinstance(retry_after, int) else 1
            raise TelegramRateLimitError(
                f"telegram rate-limited: {description}" if description else "telegram rate-limited",
                retry_after=seconds,
                context=context,
            )
        raise TelegramApiError(
            f"telegram API error: {description}" if description else "telegram API error",
            context=context,
        )

    @staticmethod
    def _as_dict(result: object, method: str) -> dict[str, object]:
        """Assert ``result`` is a JSON object (a Telegram Message / WebhookInfo / …)."""
        if not isinstance(result, dict):
            raise TelegramApiError(
                "telegram returned an unexpected result shape", context={"method": method}
            )
        return result

    async def get_me(self) -> dict[str, object]:
        """Return the bot's own ``User`` (``getMe``) — validates the token + reads the @username."""
        return self._as_dict(await self._call("getMe"), "getMe")

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict[str, object]:
        """Send a text message (``sendMessage``) and return the sent ``Message``.

        ``parse_mode`` is ``"HTML"`` for the bold name tag (D-C2-5); omitted for
        plain text. ``reply_to_message_id`` threads the reply where set.
        """
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = int(reply_to_message_id)
        return self._as_dict(await self._call("sendMessage", payload), "sendMessage")

    async def send_chat_action(self, *, chat_id: str, action: str = "typing") -> None:
        """Send a chat action (``sendChatAction``) — the "typing…" working cue (D-C2-4)."""
        await self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def set_webhook(
        self,
        *,
        url: str,
        secret_token: str | None = None,
        allowed_updates: list[str] | None = None,
    ) -> None:
        """Register the webhook (``setWebhook``) with the secret token (D-C2-2)."""
        payload: dict[str, object] = {"url": url}
        if secret_token is not None:
            payload["secret_token"] = secret_token
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        await self._call("setWebhook", payload)

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        """Remove the webhook (``deleteWebhook``) — e.g. switching to long-poll."""
        await self._call("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 0,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, object]]:
        """Long-poll for updates (``getUpdates``) — the dev transport (D-C2-1).

        ``offset = last_update_id + 1`` acks consumed updates; ``timeout`` is the
        long-poll wait (seconds). Returns the list of raw ``Update`` objects.
        """
        payload: dict[str, object] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        result = await self._call("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramApiError(
                "telegram getUpdates returned a non-list result", context={"method": "getUpdates"}
            )
        updates: list[dict[str, object]] = []
        for item in result:
            if isinstance(item, dict):
                updates.append(item)
        return updates
