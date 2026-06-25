"""Telegram deep-link account linking (Spec C2 T6, C2-R-2) — the carrier, not the lifecycle.

C1 owns the linking *lifecycle* (issue → redeem → bind → resolve → unlink) and ALL
its security (C1-D-5): the token is ``secrets.token_urlsafe(32)`` (~256 bits,
unguessable), stored only as a sha256 hash, **single-use** (consumed atomically on
redeem), **short-TTL**, and **platform-bound** — every violation raises
:class:`~persona_connectors.errors.LinkTokenInvalidError` (fail-closed). C2 must
**reuse** that — never reinvent the bind.

This module is only Telegram's **carrier** around it (C2-R-2): the deep link
``t.me/<bot>?start=<token>`` and the ``/start <token>`` redeem. **The token rides
visibly in the URL** (Telegram logs/caches it), so the whole safety argument is the
token's properties — which is exactly why the bind goes through C1's
``LinkingService`` and the issued token carries nothing but the opaque binding
reference. The deep link is sized to Telegram's constraint (the start param is
``[A-Za-z0-9_-]``, ≤64 chars — and ``token_urlsafe(32)`` ~43 base64url chars fits
that charset+length exactly, C1-D-5).

**api-free** (the reversibility ideal): pure carrier logic + C1's owned-surface
``LinkingService``; no ``persona_api``. The authenticated *issue route* (Clerk JWT →
owner via the core ``jwt_verifier``) is mounted by the transport (T7); this module
provides the service it calls.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from persona_connectors.errors import LinkTokenInvalidError
from persona_connectors.telegram.inbound import PLATFORM

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from persona_connectors.domain.linking import LinkingService

__all__ = [
    "RedeemResult",
    "RedeemStatus",
    "TelegramLinkingService",
    "build_deep_link",
    "parse_start_command",
]

_START = "/start"
# Telegram's deep-link start-param constraint (C2-R-2): A-Z a-z 0-9 _ - , ≤64 chars.
_DEEP_LINK_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_LINKED_MESSAGE = "You're linked! Just message a persona by name to start."
_FAILED_MESSAGE = (
    "That link didn't work — it may have expired or already been used. "
    "Generate a fresh link from your Open Persona settings and try again."
)


def build_deep_link(bot_username: str, token: str) -> str:
    """Build the ``t.me/<bot>?start=<token>`` deep link (C2-R-2).

    Validates the token is deep-link-safe (Telegram's ``[A-Za-z0-9_-]`` ≤64
    charset) and fails closed if not — a token that wouldn't survive the URL must
    never be handed out (defensive; C1's ``token_urlsafe(32)`` always fits).

    Raises:
        LinkTokenInvalidError: The token is not deep-link-safe (a misconfiguration).
    """
    if not _DEEP_LINK_TOKEN.match(token):
        raise LinkTokenInvalidError(
            "link token is not deep-link-safe", context={"platform": PLATFORM}
        )
    username = bot_username.lstrip("@")
    return f"https://t.me/{username}?start={token}"


def parse_start_command(text: str) -> str | None:
    """Extract the deep-link token from a ``/start <token>`` message, else ``None``.

    Handles ``/start <token>`` and ``/start@botname <token>``; a bare ``/start`` (no
    token) or any non-start text returns ``None`` (not a redeem attempt). Matches
    the command exactly — ``/started foo`` is NOT a start command.
    """
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    command = parts[0]
    if command != _START and not command.startswith(f"{_START}@"):
        return None
    if len(parts) < 2:
        return None
    return parts[1].strip() or None


class RedeemStatus(StrEnum):
    """The outcome of a ``/start`` message for linking.

    Values:
        linked: A valid token redeemed — the Telegram identity is now bound.
        failed: A token was presented but rejected (expired / used / unknown /
            wrong-platform) — fail-closed, no bind.
        not_a_link_attempt: Not a ``/start <token>`` (a bare ``/start`` or normal
            text) — the flow handles it normally (greet / link-instruction).
    """

    linked = "linked"
    failed = "failed"
    not_a_link_attempt = "not_a_link_attempt"


class RedeemResult(BaseModel):
    """The result of attempting to redeem a ``/start`` message (T6).

    Attributes:
        status: The :class:`RedeemStatus`.
        owner_id: The bound Persona-user owner on ``linked`` (so the flow can greet
            / foreground); ``None`` otherwise.
        message: The product-voice reply to send (``None`` for ``not_a_link_attempt``
            — the flow composes that path's reply).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: RedeemStatus
    owner_id: str | None = None
    message: str | None = None


class TelegramLinkingService:
    """Telegram's carrier around C1's :class:`LinkingService` (issue link / redeem).

    Holds no state beyond its injected C1 ``LinkingService`` + the bot @username
    (for the deep link). The actual token generation, single-use consumption, TTL,
    and bind all live in C1 — this only wraps them in Telegram's deep-link carrier.
    """

    def __init__(self, *, linking: LinkingService, bot_username: str) -> None:
        self._linking = linking
        self._bot_username = bot_username

    def issue_deep_link(self, *, owner_id: str, now: datetime, ttl: timedelta) -> str:
        """Issue a one-time link for ``owner_id`` and return the Telegram deep link.

        Delegates token generation to C1 (single-use, short-TTL, unguessable), then
        wraps the opaque token in ``t.me/<bot>?start=<token>``. The plaintext token
        is only ever carried in this URL (C1 stores only its hash).
        """
        token = self._linking.issue(owner_id=owner_id, platform=PLATFORM, now=now, ttl=ttl)
        return build_deep_link(self._bot_username, token)

    def redeem(self, *, plaintext_token: str, platform_identity: str, now: datetime) -> str:
        """Redeem a token and bind the Telegram identity to its owner (via C1).

        Thin pass-through to C1's ``redeem_and_bind`` — which validates (known,
        pending, unexpired, platform-matched), consumes single-use, and binds with
        the ``UNIQUE(platform, platform_identity)`` guard. Returns the owner id.

        Raises:
            LinkTokenInvalidError: Any validation failure — a forged / stale /
                replayed / wrong-platform token never binds (fail-closed).
        """
        return self._linking.redeem_and_bind(
            plaintext_token=plaintext_token,
            platform=PLATFORM,
            platform_identity=platform_identity,
            now=now,
        )

    def redeem_start_command(
        self, *, text: str, platform_identity: str, now: datetime
    ) -> RedeemResult:
        """Handle a ``/start`` message: redeem its token if present (T6 entry for the flow).

        Returns ``not_a_link_attempt`` for a bare ``/start`` / non-start text (the
        flow handles those), ``linked`` (+ owner_id + confirmation) on a valid
        redeem, or ``failed`` (+ friendly retry copy) when the token is rejected —
        never a partial bind (C1 raises before binding on any violation).
        """
        token = parse_start_command(text)
        if token is None:
            return RedeemResult(status=RedeemStatus.not_a_link_attempt)
        try:
            owner_id = self.redeem(
                plaintext_token=token, platform_identity=platform_identity, now=now
            )
        except LinkTokenInvalidError:
            return RedeemResult(status=RedeemStatus.failed, message=_FAILED_MESSAGE)
        return RedeemResult(status=RedeemStatus.linked, owner_id=owner_id, message=_LINKED_MESSAGE)
