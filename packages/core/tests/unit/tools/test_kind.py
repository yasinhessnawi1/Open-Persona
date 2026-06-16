"""Capability-kind resolution (spec 30 T01, D-30-1).

Asserts the four-value taxonomy each dispatched tool name resolves to, including
the named MCP server cases and the BYO/unknown-server default.
"""

from __future__ import annotations

import pytest
from persona.tools import Toolbox, resolve_tool_kind


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        # built-in platform tools
        ("web_search", "builtin"),
        ("code_execution", "builtin"),
        ("file_read", "builtin"),
        # the skill-activation tool renders as a skill, not a tool
        ("use_skill", "skill"),
        # authored, default-enabled built-in MCP servers
        ("mcp:time:get_current_time", "mcp:builtin"),
        ("mcp:calculator:evaluate", "mcp:builtin"),
        ("mcp:filesystem:read_file", "mcp:builtin"),
        # authored built-in server that is opt-in (not default-enabled)
        ("mcp:weather:forecast", "mcp:optional"),
        # external (bring-your-own / operator-configured) catalog servers
        ("mcp:fetch:get", "mcp:optional"),
        ("mcp:github:create_issue", "mcp:optional"),
        # a user-supplied server not in the built-in catalog → opt-in by nature
        ("mcp:my_private_server:do_thing", "mcp:optional"),
        # unknown / hallucinated name → safe builtin default (never raises)
        ("totally_unknown_tool", "builtin"),
    ],
)
def test_resolve_tool_kind_maps_name_to_provider_kind(tool_name: str, expected: str) -> None:
    assert resolve_tool_kind(tool_name) == expected


def test_resolve_tool_kind_handles_malformed_mcp_name_without_raising() -> None:
    # A bare "mcp:" prefix with no server segment must not crash — it is an
    # unknown server, which is the opt-in default.
    assert resolve_tool_kind("mcp:") == "mcp:optional"


def test_toolbox_kind_for_delegates_to_resolver() -> None:
    box = Toolbox([], allow_list=[])
    assert box.kind_for("web_search") == "builtin"
    assert box.kind_for("use_skill") == "skill"
    assert box.kind_for("mcp:time:now") == "mcp:builtin"
    assert box.kind_for("mcp:weather:forecast") == "mcp:optional"
