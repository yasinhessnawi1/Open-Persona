"""The Telegram connector — C1 ``Connector`` + C0 ``MessageDeliverer`` (Spec C2 T5).

One object that satisfies both seams the framework expects of an adapter:

- **C1's ``Connector``** (``platform`` / ``capabilities`` / :meth:`send` /
  :meth:`start` / :meth:`close`) — :meth:`send` lowers a
  :class:`~persona_connectors.domain.normalise.NormalisedOutbound` to Telegram
  message(s) via :func:`~persona_connectors.telegram.render.render_outbound`
  (HTML bold name tag + UTF-16 splitting) and delivers them, reporting a
  :class:`~persona.delivery.DeliveryResult` (never a silent drop —
  rate-limit → ``pending``, rejection → ``failed``, D-C1-X-platform-rejection).

- **C0's ``MessageDeliverer``** (:meth:`deliver`) — what registers into C0's
  ``DeliveryRouter`` under the ``"telegram"`` key. This is the **GAP-A bridge**
  (D-C2-X-gap-a-resolve-channel): a C0 ``OriginatedMessage`` carries only the
  internal ``conversation_id``, so :meth:`deliver` owner-scopes, calls the C1
  framework's ``ConversationStateStore.resolve_channel`` to get the platform
  ``(platform, channel_key)``, assembles the ``NormalisedOutbound``, and hands off
  to :meth:`send`. No resolvable channel →
  ``pending`` (never lost).

**api-free** (the thin-adapter / reversibility ideal, C1-D-1): it depends only on
C1's owned-surface ports + persona-core contracts + the Telegram client. The
owner-scope is injected (the composition root supplies
``ConnectorComposition.owner_scope``), so this module never imports ``persona_api``;
the no-streaming typing indicator + the inbound receive transport are the flow's
concerns (T7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.delivery import DeliveryOutcome, DeliveryResult

from persona_connectors.domain.normalise import Capabilities, NormalisedOutbound
from persona_connectors.errors import TelegramApiError, TelegramRateLimitError
from persona_connectors.telegram.client import TELEGRAM_MAX_MESSAGE_CHARS
from persona_connectors.telegram.inbound import PLATFORM
from persona_connectors.telegram.render import PARSE_MODE_HTML, render_outbound

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from persona.schema.origination import OriginatedMessage

    from persona_connectors.domain.conversation_model import ConversationStateStore
    from persona_connectors.telegram.client import TelegramClient

__all__ = ["TELEGRAM_CAPABILITIES", "TelegramConnector"]

# Telegram's channel capabilities (Spec C2, the decisions table). Rich formatting
# (HTML) + a typing affordance + realtime push; speaks as itself (no dedicated
# author slot → the bold-prefix render tier); no WhatsApp-style send window; the
# 4096 cap drives the splitter. ``encoding_sensitive=False`` (the splitter still
# budgets in UTF-16 units — D-C2-3); 1:1 text only in v1 (no threads).
TELEGRAM_CAPABILITIES = Capabilities(
    supports_rich_formatting=True,
    supports_author_affordance=False,
    supports_threads=False,
    supports_typing_indicator=True,
    is_realtime_push=True,
    can_initiate_freely=True,
    max_body_chars=TELEGRAM_MAX_MESSAGE_CHARS,
    encoding_sensitive=False,
    requires_delivery_auth=False,
)


class TelegramConnector:
    """The Telegram adapter — implements C1's ``Connector`` + C0's ``MessageDeliverer``.

    Holds no global state. Dependencies (the client, the conversation-state store
    for the GAP-A reverse lookup, and the owner-scope factory) are injected by the
    composition root — keeping this api-free (the reversibility ideal).
    """

    def __init__(
        self,
        *,
        client: TelegramClient,
        conversation_store: ConversationStateStore,
        owner_scope: Callable[[str], AbstractContextManager[None]],
        capabilities: Capabilities = TELEGRAM_CAPABILITIES,
    ) -> None:
        self.platform = PLATFORM
        self.capabilities = capabilities
        self._client = client
        self._store = conversation_store
        self._owner_scope = owner_scope

    async def send(self, outbound: NormalisedOutbound) -> DeliveryResult:
        """Deliver a reply / originated message to Telegram, reporting the outcome.

        Lowers the semantic name tag to HTML bold + splits to ≤4096 UTF-16 units
        (render), then sends the part(s). The name header rides the first part; the
        reply reference (if any) threads the first part only. Maps Telegram's
        rate-limit to ``pending`` (retryable) and any other rejection to ``failed``
        — **never a silent drop** (D-C1-X-platform-rejection); the bot token never
        reaches the ``detail`` (the client already guarantees it never surfaces).
        """
        parts = render_outbound(outbound.persona, outbound.text)
        try:
            for index, part in enumerate(parts):
                await self._client.send_message(
                    chat_id=outbound.conversation_key,
                    text=part,
                    parse_mode=PARSE_MODE_HTML,
                    reply_to_message_id=outbound.reply_to_message_id if index == 0 else None,
                )
        except TelegramRateLimitError:
            return DeliveryResult(
                outcome=DeliveryOutcome.PENDING,
                channel=PLATFORM,
                detail="telegram rate-limited; retry later",
            )
        except TelegramApiError:
            return DeliveryResult(
                outcome=DeliveryOutcome.FAILED, channel=PLATFORM, detail="telegram send rejected"
            )
        return DeliveryResult(outcome=DeliveryOutcome.DELIVERED, channel=PLATFORM)

    async def deliver(self, message: OriginatedMessage) -> DeliveryResult:
        """Deliver a C0-originated message to Telegram (the GAP-A bridge).

        Resolves the originated message's internal ``conversation_id`` to its
        platform channel (owner-scoped, via the framework's ``resolve_channel``),
        assembles a ``NormalisedOutbound``, and sends it. No conversation / no
        resolvable Telegram channel → ``pending`` (durably present, never lost —
        the C0 no-silent-drop contract).
        """
        if message.conversation_id is None:
            return DeliveryResult(
                outcome=DeliveryOutcome.PENDING, channel=PLATFORM, detail="no conversation"
            )
        with self._owner_scope(message.owner_user_id):
            ref = self._store.resolve_channel(conversation_id=message.conversation_id)
        if ref is None or ref.platform != PLATFORM:
            return DeliveryResult(
                outcome=DeliveryOutcome.PENDING,
                channel=PLATFORM,
                detail="no connector channel for conversation",
            )
        outbound = NormalisedOutbound(
            persona=message.persona,
            text=message.content,
            conversation_key=ref.channel_key,
            reply_to_message_id=None,
        )
        return await self.send(outbound)

    async def start(self) -> None:
        """No-op in v1 — the inbound receive transport + webhook registration land in T7/T8.

        The outbound side needs no persistent connection (each send is a stateless
        Bot API call over the injected client, whose lifecycle the composition root
        owns). Present to satisfy the ``Connector`` protocol; the receive lifecycle
        is the transport's concern.
        """

    async def close(self) -> None:
        """No-op in v1 — the injected HTTP client's lifecycle is owned by the composition root."""
