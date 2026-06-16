"""Capability-kind resolution — map a tool name to its provider kind (spec 30 T01).

The unified tool surface (Spec 26/27) dispatches built-in tools, skills, and
``mcp:server:tool`` MCP tools through one :class:`~persona.tools.Toolbox`. The
frontend (Spec 30) badges each in-chat call by its **source**; the kind is not
present where the SSE event is constructed (the event site sees only a tool
name), so it is resolved from the name here and threaded onto the event payload
(D-30-1). This is the single authoritative place the four-value taxonomy is
derived (DRY) — the same taxonomy the recommender's ``provider`` tag uses.

Taxonomy (D-30-1):

- ``builtin`` — a platform tool (``web_search``, ``code_execution``, …) or any
  unrecognised name (the safe default — never raises).
- ``skill`` — the synthetic ``use_skill`` activation tool (spec 04).
- ``mcp:builtin`` — an ``mcp:<server>:<tool>`` call whose server is an authored,
  default-enabled built-in MCP server (catalog ``kind="builtin"`` + default).
- ``mcp:optional`` — any other MCP call: an opt-in built-in server, or a
  user-supplied bring-your-own server not in the built-in catalog (opt-in by
  nature, spec 30 Cluster C).
"""

from __future__ import annotations

from typing import Literal

from persona.tools.mcp.catalog import mcp_server_entry, recommender_provider_tag

__all__ = ["ToolKind", "resolve_tool_kind"]

ToolKind = Literal["builtin", "skill", "mcp:builtin", "mcp:optional"]

#: The skill-activation tool (spec 04); rendered as a ``skill`` call, not a tool.
_USE_SKILL_TOOL = "use_skill"
_MCP_PREFIX = "mcp:"


def resolve_tool_kind(tool_name: str) -> ToolKind:
    """Resolve a dispatched tool name to its capability kind (D-30-1).

    Pure + total: an unknown name resolves to ``"builtin"`` rather than raising,
    so badging never crashes a turn.

    Args:
        tool_name: The dispatched tool name as it appears on the call/result
            event (e.g. ``"web_search"``, ``"use_skill"``, ``"mcp:time:now"``).

    Returns:
        One of the four :data:`ToolKind` values.
    """
    if tool_name.startswith(_MCP_PREFIX):
        # mcp:<server>:<tool> — the server name is the second colon-segment.
        parts = tool_name.split(":", 2)
        server = parts[1] if len(parts) >= 2 else ""
        entry = mcp_server_entry(server)
        if entry is None:
            # Unknown server: a user-supplied BYO server (spec 30) — opt-in.
            return "mcp:optional"
        # recommender_provider_tag returns exactly "mcp:builtin" | "mcp:optional".
        return "mcp:builtin" if recommender_provider_tag(entry) == "mcp:builtin" else "mcp:optional"
    if tool_name == _USE_SKILL_TOOL:
        return "skill"
    return "builtin"
