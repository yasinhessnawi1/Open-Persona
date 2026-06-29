"""Tests for the ``mcp_search`` built-in tool (Spec N4, Group A).

``mcp_search`` lets a persona search the mirrored MCP catalog by capability /
keyword and get candidate apps — friendly name, what it does, what it requires
(requirement **names only, never secret values**, N4-D-3 / criterion 3).
"""

from __future__ import annotations

import pytest
from persona.tools.builtin.mcp_search import make_mcp_search_tool
from persona.tools.mcp.catalog import (
    MCPCatalog,
    MCPSecretField,
    MCPServerCatalogEntry,
)
from persona.tools.protocol import AsyncTool


def _entry(
    name: str,
    *,
    description: str = "",
    keywords: tuple[str, ...] = (),
    capability: str = "",
    display_name: str = "",
    required_env: tuple[str, ...] = (),
    secrets: tuple[MCPSecretField, ...] = (),
    kind: str = "external",
    risk: str = "low",
) -> MCPServerCatalogEntry:
    return MCPServerCatalogEntry(
        name=name,
        description=description,
        kind=kind,  # type: ignore[arg-type]
        risk=risk,  # type: ignore[arg-type]
        keywords=keywords,
        capability=capability,
        display_name=display_name,
        required_env=required_env,
        secrets=secrets,
    )


def _catalog(*entries: MCPServerCatalogEntry) -> MCPCatalog:
    return MCPCatalog(servers={e.name: e for e in entries})


_GITHUB = _entry(
    "github",
    display_name="GitHub",
    description="Create issues, pull requests, and manage repositories.",
    keywords=("github", "issue", "pull request", "repository"),
    capability="file GitHub issues and manage repositories",
    required_env=("GITHUB_PERSONAL_ACCESS_TOKEN",),
    secrets=(
        MCPSecretField(
            name="github.personal_access_token",
            env="GITHUB_PERSONAL_ACCESS_TOKEN",
            example="<YOUR_TOKEN>",
            description="Create a fine-grained PAT at github.com/settings/tokens.",
        ),
    ),
)
_WEATHER = _entry(
    "weather",
    display_name="Weather",
    description="Look up the current weather and forecasts for a city.",
    keywords=("weather", "forecast", "temperature"),
    capability="look up the weather",
)


class TestRanking:
    @pytest.mark.asyncio
    async def test_matching_app_ranks_first(self) -> None:
        tool = make_mcp_search_tool(catalog=_catalog(_WEATHER, _GITHUB))
        result = await tool.execute(query="file a github issue")

        assert result.is_error is False
        assert result.data is not None
        results = result.data["results"]
        assert results, "expected at least one match"
        assert results[0]["name"] == "github"

    @pytest.mark.asyncio
    async def test_no_match_is_empty_not_error(self) -> None:
        tool = make_mcp_search_tool(catalog=_catalog(_WEATHER, _GITHUB))
        result = await tool.execute(query="quantum chromodynamics simulator")

        assert result.is_error is False
        assert result.data is not None
        assert result.data["results"] == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_no_matches_gracefully(self) -> None:
        tool = make_mcp_search_tool(catalog=_catalog(_WEATHER, _GITHUB))
        result = await tool.execute(query="   ")

        assert result.is_error is False
        assert result.data is not None
        assert result.data["results"] == []

    @pytest.mark.asyncio
    async def test_top_k_caps_and_clamps(self) -> None:
        many = _catalog(
            *[_entry(f"app{i}", description="weather forecast tool") for i in range(20)]
        )
        tool = make_mcp_search_tool(catalog=many)

        # top_k honored
        small = await tool.execute(query="weather", top_k=3)
        assert small.data is not None
        assert len(small.data["results"]) == 3
        assert small.data["truncated"] is True

        # an absurd top_k clamps to the hard cap (never dumps the whole catalog)
        big = await tool.execute(query="weather", top_k=10_000)
        assert big.data is not None
        assert len(big.data["results"]) <= 10


class TestNoSecretLeak:
    @pytest.mark.asyncio
    async def test_requirements_are_names_only_never_values(self) -> None:
        tool = make_mcp_search_tool(catalog=_catalog(_GITHUB))
        result = await tool.execute(query="github")

        assert result.data is not None
        item = result.data["results"][0]
        requires = item["requires"]
        # env var names + secret key/env names are surfaced...
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" in requires["env"]
        assert requires["secrets"][0]["name"] == "github.personal_access_token"
        assert requires["secrets"][0]["env"] == "GITHUB_PERSONAL_ACCESS_TOKEN"
        # ...but NO value-bearing key exists anywhere in the requirement shape.
        secret_keys = set(requires["secrets"][0])
        assert secret_keys == {"name", "env"}, secret_keys
        assert "value" not in secret_keys
        assert "example" not in secret_keys

    @pytest.mark.asyncio
    async def test_secret_example_placeholder_never_in_output(self) -> None:
        # Even the placeholder `example` must not appear in any model-facing field.
        tool = make_mcp_search_tool(catalog=_catalog(_GITHUB))
        result = await tool.execute(query="github")

        assert "<YOUR_TOKEN>" not in result.content
        assert result.data is not None
        assert "<YOUR_TOKEN>" not in str(result.data)


class TestContract:
    def test_is_async_tool(self) -> None:
        tool = make_mcp_search_tool(catalog=_catalog(_GITHUB))
        assert isinstance(tool, AsyncTool)
        assert tool.name == "mcp_search"

    @pytest.mark.asyncio
    async def test_result_carries_friendly_metadata_and_trust_signals(self) -> None:
        tool = make_mcp_search_tool(catalog=_catalog(_GITHUB))
        result = await tool.execute(query="github")

        assert result.data is not None
        item = result.data["results"][0]
        assert item["display_name"] == "GitHub"
        assert "issues" in item["description"].lower()
        # trust signals are present so the proposal can be honest
        for key in ("server_type", "risk", "signed", "source_project", "source_commit"):
            assert key in item

    @pytest.mark.asyncio
    async def test_injected_catalog_is_searched(self) -> None:
        only = _catalog(_entry("acme", description="the acme widget app", keywords=("acme",)))
        tool = make_mcp_search_tool(catalog=only)
        result = await tool.execute(query="acme")

        assert result.data is not None
        assert [r["name"] for r in result.data["results"]] == ["acme"]
