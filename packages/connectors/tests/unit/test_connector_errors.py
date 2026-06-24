"""ConnectorError — the connector framework's domain-exception base (C1 T1).

Per ENGINEERING_STANDARDS: domain exceptions, never bare ValueError/RuntimeError;
a ``context: dict[str, str]`` keyword so log messages stay structured. C1's base
extends persona-core's ``PersonaError`` (the shared root) so the whole stack
catches one hierarchy. This module is import-decoupled from persona_api.
"""

from __future__ import annotations

from persona.errors import PersonaError
from persona_connectors.errors import ConnectorError


def test_connector_error_is_a_persona_error() -> None:
    """The base sits under persona-core's PersonaError hierarchy (one root)."""
    assert issubclass(ConnectorError, PersonaError)


def test_connector_error_carries_structured_context() -> None:
    """The ``context: dict[str, str]`` keyword keeps log records structured."""
    err = ConnectorError("inbound rejected", context={"platform": "telegram"})
    assert err.context == {"platform": "telegram"}
    assert "platform=telegram" in str(err)


def test_connector_error_context_defaults_empty() -> None:
    """Context is optional — absent means an empty dict, never None."""
    err = ConnectorError("boom")
    assert err.context == {}
