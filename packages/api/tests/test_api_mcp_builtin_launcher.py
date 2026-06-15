"""Unit tests for the built-in MCP supervisor (Spec 27 T4) — no subprocesses."""

from __future__ import annotations

import os

import pytest
from persona_api.mcp import BuiltinMCPSupervisor


def test_construction_spawns_nothing() -> None:
    # D-27-3: lazy — registering enabled servers must not start any process.
    sup = BuiltinMCPSupervisor(("time", "calculator", "filesystem"))
    assert sup.running_server_count == 0
    assert sup.enabled_servers == ("time", "calculator", "filesystem")


def test_only_authored_names_are_enabled() -> None:
    # github/fetch are external (BYO) — never launchable here.
    sup = BuiltinMCPSupervisor(("time", "github", "fetch"))
    assert sup.enabled_servers == ("time",)


def test_needed_builtins_maps_mcp_prefixed_tools() -> None:
    sup = BuiltinMCPSupervisor(("time", "calculator", "filesystem"))
    declared = ["web_search", "mcp:calculator:calculate", "mcp:time:datetime", "file_read"]
    assert sup.needed_builtins(declared) == {"calculator", "time"}


def test_needed_builtins_ignores_unenabled_and_external() -> None:
    sup = BuiltinMCPSupervisor(("calculator",))
    declared = ["mcp:time:datetime", "mcp:github:create_issue", "mcp:calculator:calculate"]
    # Only the enabled built-in is needed; time (not enabled) + github (external) drop.
    assert sup.needed_builtins(declared) == {"calculator"}


@pytest.mark.asyncio
async def test_resolve_with_no_mcp_tools_spawns_nothing() -> None:
    sup = BuiltinMCPSupervisor(("time", "calculator"))
    # 'calculator' here is the builtin TOOL name, not an mcp: server reference.
    urls = await sup.resolve(["web_search", "calculator"])
    assert urls == {}
    assert sup.running_server_count == 0


@pytest.mark.asyncio
async def test_ensure_unknown_server_returns_none() -> None:
    sup = BuiltinMCPSupervisor(("time",))
    assert await sup.ensure("weather") is None  # not enabled


def test_off_loopback_host_is_forced_to_loopback() -> None:
    # D-27-12: never bind off-box, even if asked.
    sup = BuiltinMCPSupervisor(("time",), host="0.0.0.0")  # noqa: S104 — asserting it's rejected
    assert sup._url(9000) == "http://127.0.0.1:9000/mcp"


@pytest.mark.skipif(not hasattr(os, "setuid"), reason="POSIX-only privilege drop")
def test_preexec_is_none_without_uid_and_set_with_uid() -> None:
    # D-27-12 third layer: optional privilege drop wires a preexec_fn only when set.
    assert BuiltinMCPSupervisor(("time",), child_uid=None)._preexec() is None
    dropper = BuiltinMCPSupervisor(("time",), child_uid=1000)._preexec()
    assert callable(dropper)
