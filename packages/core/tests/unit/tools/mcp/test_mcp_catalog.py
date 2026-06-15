"""Unit tests for the built-in MCP server catalog (Spec 27 T2, D-27-1)."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest
from persona.tools.mcp.catalog import (
    BUILTIN_MCP_CATALOG,
    MCPServerCatalogEntry,
    authored_server_names,
    default_enabled_server_names,
    known_mcp_server_names,
    load_mcp_catalog,
    mcp_server_entry,
    recommender_provider_tag,
)
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path


def test_bundled_catalog_loads_the_six_known_servers() -> None:
    assert known_mcp_server_names() == {
        "time",
        "calculator",
        "filesystem",
        "weather",
        "fetch",
        "github",
    }


def test_default_enabled_is_the_low_risk_authored_subset() -> None:
    # D-27-4: safe subset = filesystem + time + calculator (catalog order).
    assert default_enabled_server_names() == ("time", "calculator", "filesystem")


def test_authored_servers_exclude_byo_external_ones() -> None:
    # D-27-1 / D-27-11: fetch + github are external (Persona ships no code).
    assert authored_server_names() == {"time", "calculator", "filesystem", "weather"}
    assert mcp_server_entry("fetch") is not None
    assert mcp_server_entry("fetch").kind == "external"  # type: ignore[union-attr]
    assert mcp_server_entry("github").kind == "external"  # type: ignore[union-attr]


def test_github_declares_its_required_token() -> None:
    github = mcp_server_entry("github")
    assert github is not None
    assert github.required_env == ("GITHUB_TOKEN",)


def test_weather_is_opt_in_not_default_enabled() -> None:
    weather = mcp_server_entry("weather")
    assert weather is not None
    assert weather.kind == "builtin"
    assert weather.default_enabled is False


def test_every_server_carries_gap_detection_keywords() -> None:
    # Spec 27 T11 (D-27-7) needs a non-empty keyword vocabulary per server.
    for entry in BUILTIN_MCP_CATALOG.servers.values():
        assert entry.keywords, f"{entry.name} has no gap-detection keywords"


def test_every_server_has_a_verb_led_capability() -> None:
    # Spec 27 T11: the consent line is "…which would let me {capability}." so the
    # capability MUST be a lower-cased verb phrase (not a capitalised noun phrase),
    # otherwise the question reads ungrammatically ("let me Current weather …").
    for entry in BUILTIN_MCP_CATALOG.servers.values():
        assert entry.capability, f"{entry.name} has no capability phrase"
        assert entry.capability[0].islower(), (
            f"{entry.name} capability must be a lower-cased verb phrase: {entry.capability!r}"
        )
        sentence = f"This would let me {entry.capability}."
        assert sentence.endswith(".")


def test_provider_tag_distinguishes_default_from_opt_in() -> None:
    # spec §2.3: default-enabled built-ins are mcp:builtin; everything that needs
    # an operator step (opt-in built-in OR external) is mcp:optional.
    assert recommender_provider_tag(mcp_server_entry("filesystem")) == "mcp:builtin"  # type: ignore[arg-type]
    assert recommender_provider_tag(mcp_server_entry("weather")) == "mcp:optional"  # type: ignore[arg-type]
    assert recommender_provider_tag(mcp_server_entry("github")) == "mcp:optional"  # type: ignore[arg-type]


def test_unknown_server_returns_none() -> None:
    assert mcp_server_entry("does-not-exist") is None


def test_entry_is_frozen_and_forbids_extra_fields() -> None:
    entry = mcp_server_entry("time")
    assert entry is not None
    with pytest.raises(ValidationError):
        MCPServerCatalogEntry.model_validate(
            {"name": "x", "description": "d", "kind": "builtin", "risk": "low", "bogus": 1}
        )


def test_malformed_catalog_fails_loud(tmp_path: Path) -> None:
    bad = tmp_path / "catalog.toml"
    bad.write_text(
        textwrap.dedent(
            """
            [server.broken]
            kind = "builtin"
            risk = "nonsense-risk-level"
            description = "d"
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_mcp_catalog(bad)
