"""The `Connector` protocol (Spec C1 T2, C1-D-6) — the provider-independent contract.

Each per-platform adapter (C2–C5) implements it: declare capabilities, send an
outbound (the C0 ``MessageDeliverer`` half — reports a ``DeliveryResult``,
pending-not-raise on unreachable), and the connection lifecycle. Inbound
normalisation is the adapter converting its platform payload → ``NormalisedInbound``
and handing it to the framework flow (T9) — so the protocol does not force a
single inbound transport (webhook / long-poll / gateway all fit).

Validated against ALL SIX via representative capability profiles. Owned surface,
api-free.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.delivery import DeliveryResult
from persona.schema.origination import PersonaIdentityTag
from persona_connectors.domain import (
    Capabilities,
    Connector,
    NormalisedOutbound,
    RenderTier,
    render_tier,
)


class _FakeConnector:
    """A minimal structural Connector (a faithful stub — no real platform SDK)."""

    def __init__(self, platform: str, capabilities: Capabilities) -> None:
        self.platform = platform
        self.capabilities = capabilities
        self.sent: list[NormalisedOutbound] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.started = False

    async def send(self, outbound: NormalisedOutbound) -> DeliveryResult:
        self.sent.append(outbound)
        return DeliveryResult(outcome="delivered", channel=self.platform)


def test_fake_connector_satisfies_the_protocol() -> None:
    """@runtime_checkable: a composition root can assert an adapter satisfies it."""
    conn = _FakeConnector("telegram", Capabilities(supports_rich_formatting=True))
    assert isinstance(conn, Connector)


def test_non_connector_does_not_satisfy_the_protocol() -> None:
    """A bare object missing the methods is not a Connector."""
    assert not isinstance(object(), Connector)


@pytest.mark.asyncio
async def test_send_returns_a_c0_delivery_result() -> None:
    """send() is the C0 MessageDeliverer half — a connector IS a deliverer (T10)."""
    conn = _FakeConnector("sms", Capabilities())
    tag = PersonaIdentityTag(persona_id="p1", display_name="Kai")
    out = NormalisedOutbound(persona=tag, text="hei", conversation_key="+47999")
    result = await conn.send(out)
    assert result.outcome.value == "delivered"
    assert conn.sent == [out]


@pytest.mark.parametrize(
    ("platform", "caps", "expected_tier"),
    [
        ("telegram", Capabilities(supports_rich_formatting=True), RenderTier.bold_prefix),
        (
            "discord",
            Capabilities(supports_rich_formatting=True, supports_author_affordance=True),
            RenderTier.author_affordance,
        ),
        ("slack", Capabilities(supports_rich_formatting=True), RenderTier.bold_prefix),
        (
            "whatsapp",
            Capabilities(supports_rich_formatting=True, can_initiate_freely=False),
            RenderTier.bold_prefix,
        ),
        (
            "sms",
            Capabilities(is_realtime_push=True, max_body_chars=160, encoding_sensitive=True),
            RenderTier.plain_prefix,
        ),
        (
            "email",
            Capabilities(
                supports_author_affordance=True, is_realtime_push=False, requires_delivery_auth=True
            ),
            RenderTier.author_affordance,
        ),
    ],
)
def test_protocol_and_ladder_fit_all_six_platforms(
    platform: str, caps: Capabilities, expected_tier: RenderTier
) -> None:
    """The make-or-break: a representative capability profile for each of the six
    satisfies the Connector protocol AND maps to the right render tier — proving
    the abstraction fits the full set, not just Telegram."""
    conn = _FakeConnector(platform, caps)
    assert isinstance(conn, Connector)
    assert render_tier(conn.capabilities) is expected_tier


def test_email_capability_profile_is_non_realtime() -> None:
    """Email — the hardest fit: non-real-time + delivery-auth, no threads assumed."""
    caps = Capabilities(
        supports_author_affordance=True, is_realtime_push=False, requires_delivery_auth=True
    )
    assert caps.is_realtime_push is False
    assert caps.requires_delivery_auth is True


def test_whatsapp_capability_profile_cannot_initiate_freely() -> None:
    """WhatsApp — the 24h-window gate surfaces as can_initiate_freely=False (the
    dynamic per-send reject mapping lives in send(), D-C1-X-platform-rejection)."""
    assert Capabilities(can_initiate_freely=False).can_initiate_freely is False


def test_now_is_unused_placeholder_guard() -> None:
    """Sanity that the tz-aware now helper is available for adapter tests later."""
    assert datetime.now(UTC).tzinfo is UTC
