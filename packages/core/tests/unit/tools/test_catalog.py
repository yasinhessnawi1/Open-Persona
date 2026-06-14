"""Tests for the known-tool catalog (spec 26 T08)."""

from __future__ import annotations

import pytest
from persona.tools.catalog import (
    TOOL_CATALOG,
    catalog_entry,
    known_tool_names,
    warn_unknown_declared_tools,
)


class TestCatalogShape:
    def test_entries_are_unique_by_name(self) -> None:
        names = [e.name for e in TOOL_CATALOG]
        assert len(names) == len(set(names))

    def test_known_names_matches_catalog(self) -> None:
        assert known_tool_names() == {e.name for e in TOOL_CATALOG}

    @pytest.mark.parametrize(
        "name",
        [
            "web_search",
            "web_fetch",
            "file_read",
            "file_write",
            "code_execution",
            "generate_image",
            "calculator",
            "datetime",
            "regex_match",
            "json_query",
            "text_diff",
            "currency_convert",
            "text_summarize",
        ],
    )
    def test_expected_tools_present(self, name: str) -> None:
        assert name in known_tool_names()

    def test_markdown_render_is_absent(self) -> None:
        # Dropped per D-26-1.
        assert "markdown_render" not in known_tool_names()

    def test_runtime_wired_flags(self) -> None:
        by_name = {e.name: e for e in TOOL_CATALOG}
        assert by_name["text_summarize"].runtime_wired is True
        assert by_name["code_execution"].runtime_wired is True
        assert by_name["generate_image"].runtime_wired is True
        assert by_name["calculator"].runtime_wired is False

    def test_every_entry_has_description(self) -> None:
        assert all(e.description for e in TOOL_CATALOG)

    def test_catalog_entry_lookup(self) -> None:
        entry = catalog_entry("calculator")
        assert entry is not None
        assert entry.category == "compute"
        assert catalog_entry("nonesuch") is None

    def test_entries_are_frozen(self) -> None:
        with pytest.raises(Exception, match="frozen|Instance is frozen"):
            TOOL_CATALOG[0].name = "mutated"  # type: ignore[misc]


class TestSoftWarn:
    def test_known_tools_produce_no_unknowns(self) -> None:
        assert warn_unknown_declared_tools(["calculator", "web_search"]) == ()

    def test_unknown_tool_reported_but_not_raised(self) -> None:
        unknown = warn_unknown_declared_tools(["calculator", "bogus_tool"])
        assert unknown == ("bogus_tool",)

    def test_mcp_bindings_are_skipped(self) -> None:
        # MCP server bindings are resolved dynamically, not from the catalog.
        unknown = warn_unknown_declared_tools(["mcp:husleietvistutvalget", "web_search"])
        assert unknown == ()

    def test_empty_allow_list(self) -> None:
        assert warn_unknown_declared_tools([]) == ()
