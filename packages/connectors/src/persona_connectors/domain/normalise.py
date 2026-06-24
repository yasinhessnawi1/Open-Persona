"""Normalisation contracts for the connector framework (Spec C1 T2, C1-D-6).

The shapes the shared inbound→route→respond→outbound flow works with, so each
per-platform adapter (C2–C5) only converts its platform format to/from these and
nothing else. The design rule (the make-or-break property): **design to the
Email/SMS floor** — the required inbound core is the six-platform intersection;
real-time push, native threading, and rich formatting are OPTIONAL capabilities a
richer platform *adds*. The outbound identity tag is SEMANTIC (C0's
:class:`~persona.schema.origination.PersonaIdentityTag`) — the framework passes
*intent* ("spoken by persona NAME"); the connector owns *presentation*, lowering
the tag to the highest tier it supports (author-affordance → bold-prefix →
plain-prefix). The core message never carries platform markup.

Owned surface — api-free (the decoupling guard enforces it). Reuses persona-core
(``persona.delivery`` / ``persona.schema.origination``); never persona-api.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime access
from enum import StrEnum

from persona.schema.origination import (  # noqa: TC002 — Pydantic field type needs runtime access
    PersonaIdentityTag,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "Capabilities",
    "NormalisedInbound",
    "NormalisedOutbound",
    "RenderTier",
    "plain_name_prefix",
    "render_tier",
]


class NormalisedInbound(BaseModel):
    """A platform message normalised to the shape the shared flow consumes.

    The **required core** is the six-platform intersection — only what every
    platform (Telegram/Discord/Slack/WhatsApp/SMS/email) guarantees. Everything
    Email and SMS lack is optional, so the contract is not Telegram-shaped.

    Attributes:
        platform: The opaque platform key (e.g. ``"telegram"``) — never branched
            on by the framework (D-08-3); the adapter owns platform logic.
        sender_id: The platform's stable per-user identity key (Telegram
            ``from.id`` / Discord user id / Slack ``(team_id,user)`` / WhatsApp
            ``wa_id`` / SMS E.164 / email address), string-normalised. The key
            identity-mapping dereferences to a Persona user (C1-D-5).
        conversation_key: The platform conversation key (native, or derived for
            flat-stream platforms like SMS/email).
        message_id: The platform's per-message unique id.
        text: The normalised plaintext body (email: extracted from MIME, quoted
            history stripped; Slack: raw mrkdwn).
        received_at: Tz-aware UTC time the message was received (ingestion-time
            fallback for platforms without a reliable timestamp).
        reply_to_message_id: OPTIONAL reply reference — absent on SMS.
        thread_id: OPTIONAL native/derived thread id — absent on SMS/WhatsApp.
        display_name: OPTIONAL sender display name where the platform exposes it.
        raw: The untouched provider payload (string map) — the escape-hatch for
            platform-specific needs (the Bot-Framework ``channelData`` precedent).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: str
    sender_id: str
    conversation_key: str
    message_id: str
    text: str
    received_at: datetime
    reply_to_message_id: str | None = None
    thread_id: str | None = None
    display_name: str | None = None
    raw: dict[str, str] = Field(default_factory=dict)

    @field_validator("received_at", mode="after")
    @classmethod
    def _received_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "naive datetime not allowed on NormalisedInbound.received_at"
            raise ValueError(msg)
        return value


class Capabilities(BaseModel):
    """What a connector's channel can do — so the flow never assumes an absent feature.

    Defaults are the **floor**: an under-declared connector degrades safely (no
    assumed features, treated as non-real-time), while remaining able to initiate
    (the common case — only WhatsApp's 24h window restricts that). A richer
    platform sets the flags it supports to ``True``.

    Attributes:
        supports_rich_formatting: Inline markup (bold) available (Telegram/Slack/
            WhatsApp/Discord); ``False`` for SMS.
        supports_author_affordance: A dedicated "who is speaking" slot — the top
            render tier (Discord embed ``author``, email ``From`` display-name).
        supports_threads: Native or reconstructable threading.
        supports_typing_indicator: A typing/presence affordance exists.
        is_realtime_push: The channel pushes inbound in real time (webhook/gateway).
            ``False`` for email (poll/async) — the flow must tolerate long gaps.
        can_initiate_freely: The channel allows a free-form outbound at any time.
            ``False`` for WhatsApp (the 24h-window / template gate — the dynamic
            per-message check + reject mapping lives in the connector's ``send``,
            D-C1-X-platform-rejection).
        max_body_chars: A hard per-message length budget (SMS segmenting); ``None``
            when effectively unbounded.
        encoding_sensitive: A non-Latin/emoji char changes the length budget (SMS
            GSM-7 → UCS-2). The connector owns segmentation.
        requires_delivery_auth: Sending needs domain auth (email SPF/DKIM/DMARC).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    supports_rich_formatting: bool = False
    supports_author_affordance: bool = False
    supports_threads: bool = False
    supports_typing_indicator: bool = False
    is_realtime_push: bool = False
    can_initiate_freely: bool = True
    max_body_chars: int | None = None
    encoding_sensitive: bool = False
    requires_delivery_auth: bool = False


class NormalisedOutbound(BaseModel):
    """A reply (or C0-originated message) to send, carrying the semantic name tag.

    Attributes:
        persona: The originating persona's identity tag (C0's
            :class:`~persona.schema.origination.PersonaIdentityTag`) — the
            SEMANTIC tag the connector lowers to its render tier so the user can
            tell which persona speaks (essential on a shared channel, criterion 8).
        text: The message body — plain, markup-free. The connector renders the
            name tag per its capabilities; this field never carries platform markup.
        conversation_key: The platform conversation key to deliver to.
        reply_to_message_id: OPTIONAL — thread the reply where the platform supports it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona: PersonaIdentityTag
    text: str
    conversation_key: str
    reply_to_message_id: str | None = None


class RenderTier(StrEnum):
    """The tier a connector uses to render the persona name tag (C1-D-6 ladder).

    Values (highest first): a dedicated author slot, a bold inline prefix, or the
    plaintext-prefix floor every connector can always fall back to.
    """

    author_affordance = "author_affordance"
    bold_prefix = "bold_prefix"
    plain_prefix = "plain_prefix"


def render_tier(capabilities: Capabilities) -> RenderTier:
    """Select the name-tag render tier for a channel's capabilities (framework-owned).

    The ladder logic is framework-owned (consistent across connectors); the actual
    per-platform rendering of the chosen tier is the connector's (C2–C5).
    """
    if capabilities.supports_author_affordance:
        return RenderTier.author_affordance
    if capabilities.supports_rich_formatting:
        return RenderTier.bold_prefix
    return RenderTier.plain_prefix


def plain_name_prefix(persona: PersonaIdentityTag, text: str) -> str:
    """Render the name tag as a plaintext prefix — the platform-agnostic floor.

    The universal fallback (the SMS shape) every connector can lower to when it
    supports no richer tier. The bold-prefix and author-affordance tiers are
    platform-specific and owned by the respective adapter.
    """
    return f"{persona.display_name}: {text}"
