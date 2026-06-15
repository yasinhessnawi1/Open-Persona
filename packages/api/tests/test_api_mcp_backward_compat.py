"""Backward-compat: MCP-less personas are unaffected by Spec 27 (AC #9).

The whole built-in-MCP machinery is opt-in and lazy: a persona that declares no
``mcp:`` tools must spawn nothing and see no MCP tools, and the default config
(``PERSONA_MCP_BUILTIN_ENABLED`` unset) must register the safe subset WITHOUT
running anything until a persona actually uses one.
"""

from __future__ import annotations

import pytest
from persona.config import PersonaCoreConfig
from persona_api.mcp import BuiltinMCPSupervisor

pytestmark = pytest.mark.asyncio


async def test_persona_without_mcp_tools_spawns_nothing() -> None:
    # Default-enabled safe subset is registered, but a persona that uses no MCP
    # resolves to no servers and starts no subprocess.
    cfg = PersonaCoreConfig(mcp_builtin_enabled=None)
    sup = BuiltinMCPSupervisor(cfg.mcp_builtin_enabled_parsed)
    try:
        urls = await sup.resolve(["web_search", "calculator", "file_read"])
        assert urls == {}
        assert sup.running_server_count == 0
    finally:
        await sup.aclose()


async def test_default_config_registers_safe_subset_but_runs_nothing() -> None:
    cfg = PersonaCoreConfig(mcp_builtin_enabled=None)
    sup = BuiltinMCPSupervisor(cfg.mcp_builtin_enabled_parsed)
    # "enabled" == registered + available, NOT running (D-27-4 / D-27-3).
    assert sup.enabled_servers == ("time", "calculator", "filesystem")
    assert sup.running_server_count == 0


async def test_operator_can_disable_all_builtins() -> None:
    cfg = PersonaCoreConfig(mcp_builtin_enabled="")
    sup = BuiltinMCPSupervisor(cfg.mcp_builtin_enabled_parsed)
    assert sup.enabled_servers == ()
    assert await sup.resolve(["mcp:time:datetime"]) == {}  # nothing to resolve
