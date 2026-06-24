"""Normalisation contracts (Spec C1 T2, C1-D-6) — validated against the floor.

The make-or-break property: a SINGLE normalised inbound shape + a SINGLE
outbound identity-tag convention fit ALL SIX platforms because the contract is
designed to the **Email/SMS floor** (async, flat, plaintext) — real-time push,
native threading, and rich formatting are OPTIONAL capabilities a richer
platform *adds*, never primitives the contract assumes. These tests pin that:
the required core is the six-platform intersection; everything email/SMS lack is
optional; the outbound tag is SEMANTIC (C0's PersonaIdentityTag) and the render
degrades gracefully (author-affordance → bold-prefix → plain-prefix).

Owned surface — api-free (the decoupling guard enforces it); reuses persona-core
(`persona.*`), never persona-api.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.delivery import DeliveryResult
from persona.schema.origination import PersonaIdentityTag
from persona_connectors.domain import (
    Capabilities,
    NormalisedInbound,
    NormalisedOutbound,
    RenderTier,
    plain_name_prefix,
    render_tier,
)
from pydantic import ValidationError

_WHEN = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


# --- NormalisedInbound: the required 6-field core (the intersection) ---


def test_inbound_required_core_is_the_six_field_intersection() -> None:
    """The required core is exactly the six fields every platform guarantees."""
    msg = NormalisedInbound(
        platform="telegram",
        sender_id="12345",
        conversation_key="chat-678",
        message_id="m-1",
        text="hello",
        received_at=_WHEN,
    )
    assert msg.platform == "telegram"
    assert msg.sender_id == "12345"
    assert msg.text == "hello"


def test_inbound_received_at_must_be_tz_aware() -> None:
    """Tz-aware UTC everywhere (the project rule, mirrors C0's OriginatedMessage)."""
    with pytest.raises(ValidationError):
        NormalisedInbound(
            platform="sms",
            sender_id="+4799999999",
            conversation_key="+4799999999",
            message_id="SM1",
            text="hi",
            received_at=datetime(2026, 6, 23, 12, 0),  # naive — rejected  # noqa: DTZ001
        )


def test_inbound_is_frozen_and_forbids_extra() -> None:
    """Boundary-crossing shape: frozen + extra='forbid' (ENG-STD)."""
    msg = NormalisedInbound(
        platform="slack",
        sender_id="U1",
        conversation_key="C1",
        message_id="ts-1",
        text="hi",
        received_at=_WHEN,
    )
    with pytest.raises(ValidationError):
        msg.text = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        NormalisedInbound(
            platform="slack",
            sender_id="U1",
            conversation_key="C1",
            message_id="ts-1",
            text="hi",
            received_at=_WHEN,
            bogus="x",  # extra — rejected
        )


# --- The floor: email & SMS construct with every optional ABSENT ---


def test_email_inbound_builds_on_the_floor_no_thread_no_reply() -> None:
    """Email: no native conversation id (header-derived key), no real-time, multi-
    part body normalised to text. It must build with NO optional fields — proving
    the contract isn't Telegram-shaped (threads/reply are optional)."""
    msg = NormalisedInbound(
        platform="email",
        sender_id="alice@example.com",
        conversation_key="<derived-thread-key>",
        message_id="<msgid@example.com>",
        text="normalised plaintext body",
        received_at=_WHEN,
    )
    assert msg.reply_to_message_id is None
    assert msg.thread_id is None
    assert msg.raw == {}


def test_sms_inbound_builds_on_the_floor_flat_stream() -> None:
    """SMS: no threading at all, no reply ref, flat (From,To) stream. Floor-builds."""
    msg = NormalisedInbound(
        platform="sms",
        sender_id="+4799999999",
        conversation_key="+4799999999|+4788888888",
        message_id="SM123",
        text="hei",
        received_at=_WHEN,
    )
    assert msg.thread_id is None
    assert msg.reply_to_message_id is None


def test_inbound_optionals_and_raw_escape_hatch_carry_when_present() -> None:
    """Richer platforms ADD capability: thread/reply refs + the raw passthrough
    (the Bot-Framework channelData precedent — D-08-3 opaque shape)."""
    msg = NormalisedInbound(
        platform="slack",
        sender_id="U1",
        conversation_key="C1",
        message_id="1700000000.000200",
        text="in thread",
        received_at=_WHEN,
        reply_to_message_id="1699999999.000100",
        thread_id="1699999999.000100",
        display_name="Alice",
        raw={"team_id": "T1"},
    )
    assert msg.thread_id == "1699999999.000100"
    assert msg.raw["team_id"] == "T1"


# --- Capabilities: defaults are the floor; platforms add ---


def test_capabilities_default_to_the_floor() -> None:
    """An under-declared connector degrades SAFELY: no assumed features, not
    real-time, but able to initiate (the common case; WhatsApp overrides)."""
    caps = Capabilities()
    assert caps.supports_rich_formatting is False
    assert caps.supports_author_affordance is False
    assert caps.supports_threads is False
    assert caps.supports_typing_indicator is False
    assert caps.is_realtime_push is False
    assert caps.can_initiate_freely is True
    assert caps.max_body_chars is None
    assert caps.encoding_sensitive is False
    assert caps.requires_delivery_auth is False


def test_capabilities_is_frozen() -> None:
    caps = Capabilities()
    with pytest.raises(ValidationError):
        caps.supports_threads = True  # type: ignore[misc]


# --- The outbound semantic tag + the degradation ladder ---


def test_outbound_carries_the_semantic_persona_identity_tag() -> None:
    """The outbound carries INTENT (C0's PersonaIdentityTag) — not platform markup.
    The connector lowers it to its render tier; the core message stays markup-free."""
    tag = PersonaIdentityTag(persona_id="p1", display_name="Astrid")
    out = NormalisedOutbound(persona=tag, text="hei", conversation_key="chat-1")
    assert out.persona.display_name == "Astrid"
    assert out.text == "hei"
    # No platform markup baked into the core message.
    assert "*" not in out.text
    assert "<b>" not in out.text


def test_render_tier_picks_author_affordance_for_discord_email() -> None:
    """Top tier: a dedicated author affordance (Discord embed author / email From)."""
    caps = Capabilities(supports_author_affordance=True, supports_rich_formatting=True)
    assert render_tier(caps) is RenderTier.author_affordance


def test_render_tier_picks_bold_prefix_for_telegram_slack_whatsapp() -> None:
    """Middle tier: a bold inline prefix where rich text exists but no author slot."""
    caps = Capabilities(supports_rich_formatting=True)
    assert render_tier(caps) is RenderTier.bold_prefix


def test_render_tier_falls_to_plain_prefix_for_sms() -> None:
    """Floor tier: a plaintext prefix (SMS — zero formatting)."""
    assert render_tier(Capabilities()) is RenderTier.plain_prefix


def test_plain_name_prefix_is_the_platform_agnostic_floor_renderer() -> None:
    """The universal fallback every connector can lower to (the SMS shape)."""
    tag = PersonaIdentityTag(persona_id="p1", display_name="Kai")
    assert plain_name_prefix(tag, "hello") == "Kai: hello"


# --- The Connector's send() reports a C0 DeliveryResult (the outbound seam) ---


def test_delivery_result_is_the_c0_outbound_contract() -> None:
    """send() returns C0's DeliveryResult so a connector IS a MessageDeliverer
    (T10) — no parallel outcome type. Sanity that the reused type is importable
    and shaped as expected (pending-not-raise is the no-silent-drop contract)."""
    result = DeliveryResult(outcome="pending", channel="telegram", detail="offline")
    assert result.outcome.value == "pending"
    assert result.channel == "telegram"
