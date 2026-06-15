"""Unit tests for the built-in ``calculator`` MCP server (Spec 27 T7)."""

from __future__ import annotations

import pytest
from persona.tools.mcp.builtin import SERVER_BUILDERS
from persona.tools.mcp.builtin.calculator_server import build


def _first_text(call_result: object) -> str:
    blocks = call_result[0] if isinstance(call_result, tuple) else call_result
    for block in blocks:  # type: ignore[union-attr]
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return ""


@pytest.mark.asyncio
async def test_build_returns_named_server_with_calculate_tool() -> None:
    server = build("127.0.0.1", 8300)
    assert server.name == "calculator"
    tools = await server.list_tools()
    assert "calculate" in {t.name for t in tools}


@pytest.mark.asyncio
async def test_evaluates_a_real_expression_via_delegation() -> None:
    server = build("127.0.0.1", 8301)
    result = await server.call_tool("calculate", {"expression": "sqrt(2) * 3 + 1"})
    assert "5.24" in _first_text(result)  # sqrt(2)*3 + 1 ≈ 5.2426...


@pytest.mark.asyncio
async def test_rejects_attribute_access_rce_attempt() -> None:
    # The Spec-26 AST whitelist denies attribute access — proven still wired.
    server = build("127.0.0.1", 8302)
    with pytest.raises(Exception, match="(?i)error|not allowed|invalid|unsupported"):
        await server.call_tool("calculate", {"expression": "(1).__class__"})


def test_calculator_is_registered_in_the_builder_registry() -> None:
    assert SERVER_BUILDERS.get("calculator") is build
