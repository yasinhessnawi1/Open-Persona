"""Unit tests for PERSONA_MCP_BUILTIN_ENABLED config parsing (Spec 27 T3, D-27-4)."""

from __future__ import annotations

import pytest
from persona.config import PersonaCoreConfig
from pydantic import ValidationError


def test_unset_defaults_to_catalog_safe_subset() -> None:
    cfg = PersonaCoreConfig(mcp_builtin_enabled=None)
    assert cfg.mcp_builtin_enabled_parsed == ("time", "calculator", "filesystem")


def test_explicit_empty_string_opts_out_of_all() -> None:
    cfg = PersonaCoreConfig(mcp_builtin_enabled="")
    assert cfg.mcp_builtin_enabled_parsed == ()


def test_explicit_subset_overrides_default() -> None:
    cfg = PersonaCoreConfig(mcp_builtin_enabled="calculator,weather")
    assert cfg.mcp_builtin_enabled_parsed == ("calculator", "weather")


def test_whitespace_and_blank_entries_are_tolerated() -> None:
    cfg = PersonaCoreConfig(mcp_builtin_enabled=" time , , calculator ")
    assert cfg.mcp_builtin_enabled_parsed == ("time", "calculator")


def test_duplicates_are_de_duplicated_in_order() -> None:
    cfg = PersonaCoreConfig(mcp_builtin_enabled="time,calculator,time")
    assert cfg.mcp_builtin_enabled_parsed == ("time", "calculator")


def test_unknown_builtin_name_fails_loud() -> None:
    with pytest.raises(ValidationError, match="unknown built-in MCP server"):
        PersonaCoreConfig(mcp_builtin_enabled="time,nonsense")


def test_external_server_name_points_to_mcp_servers() -> None:
    # fetch/github are BYO external — enabling them here is a misconfiguration.
    with pytest.raises(ValidationError, match="PERSONA_MCP_SERVERS"):
        PersonaCoreConfig(mcp_builtin_enabled="github")


def test_env_var_drives_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_MCP_BUILTIN_ENABLED", "filesystem")
    cfg = PersonaCoreConfig()
    assert cfg.mcp_builtin_enabled_parsed == ("filesystem",)
