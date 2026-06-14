"""Tests for the json_query built-in tool (spec 26 T05)."""

from __future__ import annotations

import json

import pytest
from persona.tools.builtin.json_query import _MAX_INPUT_LEN, make_json_query_tool
from persona.tools.protocol import AsyncTool


class TestJsonQueryTool:
    def test_is_async_tool(self) -> None:
        tool_inst = make_json_query_tool()
        assert isinstance(tool_inst, AsyncTool)
        assert tool_inst.name == "json_query"

    @pytest.mark.asyncio
    async def test_simple_field_access(self) -> None:
        tool_inst = make_json_query_tool()
        result = await tool_inst.execute(json_input='{"a": {"b": 42}}', query="a.b")
        assert result.is_error is False
        assert result.data == {"matched": True, "result": 42}
        assert result.content == "42"

    @pytest.mark.asyncio
    async def test_list_projection(self) -> None:
        tool_inst = make_json_query_tool()
        payload = json.dumps({"items": [{"name": "x"}, {"name": "y"}]})
        result = await tool_inst.execute(json_input=payload, query="items[].name")
        assert result.is_error is False
        assert result.data is not None
        assert result.data["result"] == ["x", "y"]

    @pytest.mark.asyncio
    async def test_no_match_returns_null_not_error(self) -> None:
        tool_inst = make_json_query_tool()
        result = await tool_inst.execute(json_input='{"a": 1}', query="a.z")
        assert result.is_error is False
        assert result.data == {"matched": False, "result": None}
        assert "No match" in result.content

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self) -> None:
        tool_inst = make_json_query_tool()
        result = await tool_inst.execute(json_input="{not valid", query="a")
        assert result.is_error is True
        assert "Invalid JSON" in result.content

    @pytest.mark.asyncio
    async def test_invalid_query_returns_error_not_raises(self) -> None:
        tool_inst = make_json_query_tool()
        result = await tool_inst.execute(json_input='{"a": 1}', query="a..b")
        assert result.is_error is True
        assert "Invalid JMESPath" in result.content

    @pytest.mark.asyncio
    async def test_oversized_input_rejected(self) -> None:
        tool_inst = make_json_query_tool()
        big = '{"x": "' + "a" * (_MAX_INPUT_LEN) + '"}'
        result = await tool_inst.execute(json_input=big, query="x")
        assert result.is_error is True
        assert "too large" in result.content

    @pytest.mark.asyncio
    async def test_object_result_serialised(self) -> None:
        tool_inst = make_json_query_tool()
        result = await tool_inst.execute(
            json_input='{"user": {"id": 1, "name": "Astrid"}}', query="user"
        )
        assert result.is_error is False
        assert result.data is not None
        assert result.data["result"] == {"id": 1, "name": "Astrid"}
