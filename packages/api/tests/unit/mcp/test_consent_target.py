"""Consent-target validation (spec 30 T10, D-30-X-mcp-gap-accept-target).

The MCP-gap accept reuses the tool-consent path with an ``mcp:<server>`` name;
the validator must admit catalog-valid built-in MCP servers AND keep rejecting
hallucinated names (and bring-your-own servers, which use the assignment path).
"""

from __future__ import annotations

import pytest
from persona_api.services.tool_consent_service import _is_valid_consent_target


@pytest.mark.parametrize(
    "name",
    [
        "web_search",  # a built-in tool
        "calculator",
        "mcp:time",  # a default-enabled built-in MCP server
        "mcp:weather",  # an opt-in built-in MCP server (still catalog-valid)
        "mcp:github",  # an external catalog server
    ],
)
def test_valid_targets(name: str) -> None:
    assert _is_valid_consent_target(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "search_legal_database",  # hallucinated tool
        "mcp:not_a_real_server",  # not in the MCP catalog (e.g. a BYO server name)
        "mcp:",  # malformed, no server
        "mcp",  # not an mcp: reference
        "",
    ],
)
def test_invalid_targets(name: str) -> None:
    assert _is_valid_consent_target(name) is False
