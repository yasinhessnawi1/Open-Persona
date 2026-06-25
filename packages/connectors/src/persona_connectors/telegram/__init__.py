"""The Telegram connector adapter (Spec C2) — the first concrete connector.

A deliberately **thin** adapter implementing C1's ``Connector`` protocol for
Telegram: it converts Telegram updates to C1's ``NormalisedInbound``, lets C1's
shared flow drive the reply, and renders C1's ``NormalisedOutbound`` back as
Telegram messages (plus Telegram's deep-link account-linking specifics).
Everything else — routing, persona selection, the conversation model, identity
mapping, C0 delivery — is C1's and is *used*, not reimplemented.

The whole adapter is **api-free** (it depends only on C1's owned-surface ports +
persona-core contracts + ``httpx``); the api-coupling that wires it to the live
service lives in :mod:`persona_connectors.composition` (the reversibility
guarantee, C1-D-1). The Bot API itself is plain JSON-over-HTTPS, so the client
talks to it with ``httpx`` directly (D-C2-X-no-new-dep).
"""

from __future__ import annotations

from persona_connectors.telegram.app import build_telegram_app
from persona_connectors.telegram.client import TelegramClient
from persona_connectors.telegram.connector import TELEGRAM_CAPABILITIES, TelegramConnector
from persona_connectors.telegram.flow import InboundFlow, TurnRequest
from persona_connectors.telegram.inbound import (
    PLATFORM,
    InboundIgnore,
    InboundNonText,
    InboundText,
    NonTextKind,
    NormalisedUpdate,
    classify_update,
)
from persona_connectors.telegram.linking import (
    RedeemResult,
    RedeemStatus,
    TelegramLinkingService,
    build_deep_link,
    parse_start_command,
)
from persona_connectors.telegram.longpoll import run_long_poll
from persona_connectors.telegram.non_text import decline_message
from persona_connectors.telegram.render import (
    PARSE_MODE_HTML,
    escape_html,
    render_outbound,
    split_text,
    utf16_length,
)
from persona_connectors.telegram.webhook import TELEGRAM_SECRET_HEADER, verify_webhook_secret

__all__ = [
    "PARSE_MODE_HTML",
    "PLATFORM",
    "TELEGRAM_CAPABILITIES",
    "TELEGRAM_SECRET_HEADER",
    "InboundFlow",
    "InboundIgnore",
    "InboundNonText",
    "InboundText",
    "NonTextKind",
    "NormalisedUpdate",
    "RedeemResult",
    "RedeemStatus",
    "TelegramClient",
    "TelegramConnector",
    "TelegramLinkingService",
    "TurnRequest",
    "build_deep_link",
    "build_telegram_app",
    "classify_update",
    "decline_message",
    "escape_html",
    "parse_start_command",
    "render_outbound",
    "run_long_poll",
    "split_text",
    "utf16_length",
    "verify_webhook_secret",
]
