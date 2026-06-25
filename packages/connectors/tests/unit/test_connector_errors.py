"""ConnectorError — the connector framework's domain-exception base (C1 T1).

Per ENGINEERING_STANDARDS: domain exceptions, never bare ValueError/RuntimeError;
a ``context: dict[str, str]`` keyword so log messages stay structured. C1's base
extends persona-core's ``PersonaError`` (the shared root) so the whole stack
catches one hierarchy. This module is import-decoupled from persona_api.
"""

from __future__ import annotations

from persona.errors import PersonaError
from persona_connectors.errors import (
    ConnectorError,
    TelegramApiError,
    TelegramRateLimitError,
)


def test_connector_error_is_a_persona_error() -> None:
    """The base sits under persona-core's PersonaError hierarchy (one root)."""
    assert issubclass(ConnectorError, PersonaError)


def test_telegram_errors_are_connector_errors() -> None:
    """C2's Telegram errors sit under the C1 ConnectorError hierarchy (one root)."""
    assert issubclass(TelegramApiError, ConnectorError)
    assert issubclass(TelegramRateLimitError, TelegramApiError)


def test_telegram_rate_limit_error_carries_retry_after() -> None:
    """The rate-limit error exposes retry_after as an attribute AND in context."""
    err = TelegramRateLimitError("throttled", retry_after=7, context={"method": "sendMessage"})
    assert err.retry_after == 7
    assert err.context == {"method": "sendMessage", "retry_after": "7"}
    assert "retry_after=7" in str(err)


def test_connector_error_carries_structured_context() -> None:
    """The ``context: dict[str, str]`` keyword keeps log records structured."""
    err = ConnectorError("inbound rejected", context={"platform": "telegram"})
    assert err.context == {"platform": "telegram"}
    assert "platform=telegram" in str(err)


def test_connector_error_context_defaults_empty() -> None:
    """Context is optional — absent means an empty dict, never None."""
    err = ConnectorError("boom")
    assert err.context == {}
