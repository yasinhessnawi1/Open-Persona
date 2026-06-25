"""Webhook secret-token validation (Spec C2 T7, D-C2-2) — the mandatory security gate.

A webhook is a **public endpoint** whose only authentication is the secret token
Telegram echoes back in the ``X-Telegram-Bot-Api-Secret-Token`` header on every
request (set via ``setWebhook(secret_token=…)``). So this check is the whole
defence against spoofed "Telegram" updates (the webhook-security risk, criterion
9). Three properties, all load-bearing:

1. **Constant-time** — compared with :func:`hmac.compare_digest`, never ``==`` (a
   plain compare leaks the secret a byte at a time via timing).
2. **Validate-before-parse** — the caller runs this BEFORE JSON-parsing the body,
   so unauthenticated input never reaches the parser.
3. **Fail-closed on an unset secret** — if no secret is configured, **reject every
   request** rather than fall open and accept all webhooks. A public endpoint with
   no auth that accepts everything is the exact hole D-C2-2 exists to prevent.

Pure + api-free.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import SecretStr

__all__ = ["TELEGRAM_SECRET_HEADER", "verify_webhook_secret"]

# The header Telegram sends the configured secret token in on every webhook request.
TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def verify_webhook_secret(configured: SecretStr | None, presented: str | None) -> bool:
    """Whether a webhook request's secret-token header is valid (D-C2-2).

    Args:
        configured: The secret set on the webhook (a ``SecretStr``), or ``None`` if
            none is configured.
        presented: The value of the ``X-Telegram-Bot-Api-Secret-Token`` header on
            the incoming request, or ``None`` if absent.

    Returns:
        ``True`` only when a secret IS configured AND the presented header matches
        it (constant-time). **Fail-closed:** an unset secret (``configured is
        None``) or an absent header returns ``False`` — the endpoint rejects rather
        than accepting unauthenticated traffic.
    """
    if configured is None or presented is None:
        return False
    return hmac.compare_digest(configured.get_secret_value(), presented)
