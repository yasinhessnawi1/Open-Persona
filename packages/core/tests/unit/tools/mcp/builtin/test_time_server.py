"""Unit tests for the built-in ``time`` MCP server (Spec 27 T6)."""

from __future__ import annotations

import pytest
from persona.tools.mcp.builtin import SERVER_BUILDERS
from persona.tools.mcp.builtin.time_server import build


@pytest.mark.asyncio
async def test_build_returns_named_server_with_datetime_tool() -> None:
    server = build("127.0.0.1", 8200)
    assert server.name == "time"
    tools = await server.list_tools()
    assert "datetime" in {t.name for t in tools}


@pytest.mark.asyncio
async def test_datetime_tool_advertises_an_input_schema() -> None:
    server = build("127.0.0.1", 8201)
    tools = await server.list_tools()
    datetime_tool = next(t for t in tools if t.name == "datetime")
    props = datetime_tool.inputSchema.get("properties", {})
    assert "operation" in props
    assert "timezone" in props


@pytest.mark.asyncio
async def test_now_operation_returns_iso_time_via_delegation() -> None:
    server = build("127.0.0.1", 8202)
    result = await server.call_tool("datetime", {"operation": "now", "timezone": "Europe/Oslo"})
    # FastMCP returns (content_blocks, structured); the text carries the ISO time.
    text = _first_text(result)
    assert "Europe/Oslo" in text


@pytest.mark.asyncio
async def test_bad_timezone_is_surfaced_as_an_error() -> None:
    server = build("127.0.0.1", 8203)
    with pytest.raises(Exception, match="[Uu]nknown timezone"):
        await server.call_tool("datetime", {"operation": "now", "timezone": "Mars/Phobos"})


def test_time_is_registered_in_the_builder_registry() -> None:
    assert SERVER_BUILDERS.get("time") is build


def _first_text(call_result: object) -> str:
    """Extract the first text block from a FastMCP call_tool result (shape-tolerant)."""
    blocks = call_result[0] if isinstance(call_result, tuple) else call_result
    for block in blocks:  # type: ignore[union-attr]
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return ""
