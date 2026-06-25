"""Telegram deep-link account linking (Spec C2 T6, C2-R-2) — carrier + adversarial.

The bind's security lives in C1's LinkingService (reused, not reinvented), so these
tests drive the REAL LinkingService over an in-memory LinkStore through C2's
Telegram carrier — proving the deep link is well-formed AND the dangerous paths
fail closed: replay, expiry, unknown, wrong-platform, and a second identity reusing
a consumed token. (The Postgres LinkStore + RLS are integration-tested in C1.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from persona_connectors.domain.linking import LinkingService, LinkRecord, LinkToken
from persona_connectors.errors import LinkTokenInvalidError
from persona_connectors.telegram.linking import (
    RedeemStatus,
    TelegramLinkingService,
    build_deep_link,
    parse_start_command,
)

_NOW = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)
_TTL = timedelta(minutes=15)


class _InMemoryLinkStore:
    """A minimal in-memory LinkStore so the REAL LinkingService logic is exercised."""

    def __init__(self) -> None:
        self.tokens: dict[str, LinkToken] = {}
        self.identities: dict[tuple[str, str], LinkRecord] = {}

    def create_token(self, token: LinkToken) -> None:
        self.tokens[token.token_hash] = token

    def get_token_by_hash(self, token_hash: str) -> LinkToken | None:
        return self.tokens.get(token_hash)

    def consume_token(self, token_hash: str, *, now: datetime) -> None:
        token = self.tokens[token_hash]
        self.tokens[token_hash] = token.model_copy(
            update={"status": "consumed", "consumed_at": now}
        )

    def bind_identity(
        self, *, platform: str, platform_identity: str, owner_id: str, now: datetime
    ) -> None:
        key = (platform, platform_identity)
        if key in self.identities and self.identities[key].status == "active":
            # The UNIQUE(platform, platform_identity) guard (C1-D-5) — surfaced here.
            msg = "platform identity already bound"
            raise LinkTokenInvalidError(msg, context={"platform": platform})
        self.identities[key] = LinkRecord(
            platform=platform,
            platform_identity=platform_identity,
            owner_id=owner_id,
            status="active",
            linked_at=now,
        )

    def get_active_identity(self, *, platform: str, platform_identity: str) -> LinkRecord | None:
        record = self.identities.get((platform, platform_identity))
        return record if record is not None and record.status == "active" else None

    def revoke_identity(
        self,
        *,
        owner_id: str,  # noqa: ARG002 — kept for LinkStore protocol conformance (unlink not exercised)
        platform: str,
        platform_identity: str,
        now: datetime,
    ) -> None:
        key = (platform, platform_identity)
        if key in self.identities:
            self.identities[key] = self.identities[key].model_copy(
                update={"status": "revoked", "revoked_at": now}
            )


def _service(bot_username: str = "my_persona_bot") -> tuple[TelegramLinkingService, LinkingService]:
    linking = LinkingService(_InMemoryLinkStore())
    return TelegramLinkingService(linking=linking, bot_username=bot_username), linking


# --- deep-link carrier ---


def test_issue_deep_link_wraps_the_token_in_a_tme_url() -> None:
    """issue_deep_link produces t.me/<bot>?start=<token> with a deep-link-safe token."""
    service, _ = _service()
    url = service.issue_deep_link(owner_id="user_a", now=_NOW, ttl=_TTL)
    assert url.startswith("https://t.me/my_persona_bot?start=")
    token = url.split("start=", 1)[1]
    # The C1 token_urlsafe(32) must fit Telegram's [A-Za-z0-9_-] ≤64 constraint.
    assert 1 <= len(token) <= 64
    assert all(c.isalnum() or c in "_-" for c in token)


def test_build_deep_link_rejects_a_non_safe_token() -> None:
    """A token that wouldn't survive the URL fails closed (defensive)."""
    with pytest.raises(LinkTokenInvalidError):
        build_deep_link("bot", "not safe/has spaces")


def test_build_deep_link_strips_leading_at() -> None:
    assert build_deep_link("@bot", "abc").startswith("https://t.me/bot?start=abc")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start abc123", "abc123"),
        ("/start@my_persona_bot abc123", "abc123"),
        ("  /start   tok  ", "tok"),
        ("/start", None),
        ("/started abc", None),
        ("hello there", None),
        ("", None),
    ],
)
def test_parse_start_command(text: str, expected: str | None) -> None:
    """Only an exact /start (optionally @bot) with a token yields the token."""
    assert parse_start_command(text) == expected


# --- redeem (happy path) ---


def test_issue_then_redeem_binds_the_identity() -> None:
    """A freshly issued token redeems once, binding the Telegram identity to its owner."""
    service, _ = _service()
    url = service.issue_deep_link(owner_id="user_a", now=_NOW, ttl=_TTL)
    token = url.split("start=", 1)[1]

    owner_id = service.redeem(plaintext_token=token, platform_identity="tg_777", now=_NOW)
    assert owner_id == "user_a"


def test_redeem_start_command_links_and_returns_confirmation() -> None:
    """The flow entry: /start <token> → linked + owner + a friendly confirmation."""
    service, _ = _service()
    url = service.issue_deep_link(owner_id="user_a", now=_NOW, ttl=_TTL)
    token = url.split("start=", 1)[1]

    result = service.redeem_start_command(
        text=f"/start {token}", platform_identity="tg_777", now=_NOW
    )
    assert result.status == RedeemStatus.linked
    assert result.owner_id == "user_a"
    assert result.message


def test_bare_start_is_not_a_link_attempt() -> None:
    """A bare /start (no token) is not a redeem — the flow handles it (greet/link-instruction)."""
    service, _ = _service()
    result = service.redeem_start_command(text="/start", platform_identity="tg_777", now=_NOW)
    assert result.status == RedeemStatus.not_a_link_attempt
    assert result.owner_id is None


# --- adversarial: the dangerous paths must fail closed ---


def test_replay_a_consumed_token_fails_closed() -> None:
    """A token redeems exactly once; a replay is rejected (single-use, no re-bind)."""
    service, _ = _service()
    token = service.issue_deep_link(owner_id="user_a", now=_NOW, ttl=_TTL).split("start=", 1)[1]
    service.redeem(plaintext_token=token, platform_identity="tg_777", now=_NOW)

    with pytest.raises(LinkTokenInvalidError):
        service.redeem(plaintext_token=token, platform_identity="tg_777", now=_NOW)


def test_replay_by_a_different_identity_cannot_bind() -> None:
    """A consumed token replayed by ANOTHER Telegram user binds nothing (no hijack)."""
    service, _ = _service()
    token = service.issue_deep_link(owner_id="user_a", now=_NOW, ttl=_TTL).split("start=", 1)[1]
    service.redeem(plaintext_token=token, platform_identity="tg_777", now=_NOW)

    result = service.redeem_start_command(
        text=f"/start {token}", platform_identity="tg_attacker", now=_NOW
    )
    assert result.status == RedeemStatus.failed
    assert result.owner_id is None


def test_expired_token_fails_closed() -> None:
    """A token presented after its TTL is rejected (no late bind)."""
    service, _ = _service()
    token = service.issue_deep_link(owner_id="user_a", now=_NOW, ttl=_TTL).split("start=", 1)[1]
    later = _NOW + timedelta(minutes=16)  # past the 15-min TTL

    result = service.redeem_start_command(
        text=f"/start {token}", platform_identity="tg_777", now=later
    )
    assert result.status == RedeemStatus.failed


def test_unknown_token_fails_closed() -> None:
    """A forged/guessed token that was never issued is rejected."""
    service, _ = _service()
    result = service.redeem_start_command(
        text="/start totally-made-up-token", platform_identity="tg_777", now=_NOW
    )
    assert result.status == RedeemStatus.failed


def test_wrong_platform_token_fails_closed() -> None:
    """A token issued for another platform cannot redeem over Telegram (platform-bound)."""
    service, linking = _service()
    # Issue a token bound to platform 'discord' directly via C1's service.
    discord_token = linking.issue(owner_id="user_a", platform="discord", now=_NOW, ttl=_TTL)
    result = service.redeem_start_command(
        text=f"/start {discord_token}", platform_identity="tg_777", now=_NOW
    )
    assert result.status == RedeemStatus.failed
