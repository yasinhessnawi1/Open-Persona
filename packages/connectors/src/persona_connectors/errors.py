"""Domain exceptions for the connector framework (Spec C1).

Per ENGINEERING_STANDARDS: domain logic raises domain exceptions, never bare
``ValueError``/``RuntimeError``; every exception carries a ``context: dict[str,
str]`` keyword so log records stay structured. ``ConnectorError`` is the C1 base
and extends persona-core's :class:`~persona.errors.PersonaError` so the whole
stack catches one hierarchy.

More specific exceptions (e.g. an unlinked-identity error, a persona-not-
addressable error) land in later tasks where they are raised — this module ships
the base so T1's surface is real. Import-decoupled from persona_api.
"""

from __future__ import annotations

from persona.errors import PersonaError

__all__ = [
    "ConnectorError",
    "IdentityNotLinkedError",
    "LinkTokenInvalidError",
    "TelegramApiError",
    "TelegramRateLimitError",
]


class ConnectorError(PersonaError):
    """Base for every connector-framework domain error (Spec C1).

    Subclasses are introduced where they are raised (identity resolution,
    persona addressing, account linking, delivery). Carries the inherited
    ``context: dict[str, str]`` for structured logging.
    """


class IdentityNotLinkedError(ConnectorError):
    """An inbound platform identity has no live (active) Persona-user binding.

    The load-bearing security invariant (C1-D-5, criteria 6/7): an unlinked (or
    revoked) identity gets a link-instruction and ZERO access — never another
    user's personas. The flow catches this and replies with the linking prompt.
    """


class LinkTokenInvalidError(ConnectorError):
    """A presented link token is unknown, expired, already consumed, or mismatched.

    Single-use + short-TTL + platform-bound (C1-D-5); any violation fails loud so
    a replayed/forged/stale token never binds an identity.
    """


class TelegramApiError(ConnectorError):
    """A Telegram Bot API call failed (Spec C2 — the adapter-boundary domain error).

    Telegram's transport faults (HTTP errors, network failures) and logical
    rejections (``{"ok": false, ...}``) are caught at the Bot API client boundary
    and re-raised as this domain error, so callers depend on our hierarchy, not on
    ``httpx`` (the ENG-STD catch-at-the-boundary rule).

    The bot token rides in the request URL (``/bot<token>/<method>``), so the
    underlying ``httpx`` exception (which carries that URL) is **never chained or
    quoted** (D-C2-X-credential): the ``context`` carries only the method name and
    the Telegram ``error_code``/status — never the URL or the token.
    """


class TelegramRateLimitError(TelegramApiError):
    """Telegram throttled the bot (HTTP 429) — back off for :attr:`retry_after` seconds.

    Telegram returns ``429`` with ``parameters.retry_after`` when a send exceeds
    the rate limits (~30/s global, 1/s per chat — C2-R-1). The send path honours
    :attr:`retry_after` (D-C2-3); a non-retryable rejection maps to a
    :class:`~persona.delivery.DeliveryResult` ``failed`` (D-C1-X-platform-rejection)
    rather than a silent drop.

    Attributes:
        retry_after: Seconds Telegram asks the caller to wait before retrying.
    """

    def __init__(
        self,
        message: str = "",
        *,
        retry_after: int,
        context: dict[str, str] | None = None,
    ) -> None:
        merged = {**(context or {}), "retry_after": str(retry_after)}
        super().__init__(message, context=merged)
        self.retry_after = retry_after
