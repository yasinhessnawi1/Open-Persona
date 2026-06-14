"""Tests for the text_diff built-in tool (spec 26 T06)."""

from __future__ import annotations

import pytest
from persona.tools.builtin.text_diff import _MAX_CHARS, make_text_diff_tool
from persona.tools.protocol import AsyncTool


class TestTextDiffTool:
    def test_is_async_tool(self) -> None:
        tool_inst = make_text_diff_tool()
        assert isinstance(tool_inst, AsyncTool)
        assert tool_inst.name == "text_diff"

    @pytest.mark.asyncio
    async def test_identical_texts_report_no_difference(self) -> None:
        tool_inst = make_text_diff_tool()
        result = await tool_inst.execute(a="line1\nline2", b="line1\nline2")
        assert result.is_error is False
        assert result.content == "No differences."
        assert result.data == {"changed": False, "added": 0, "removed": 0}

    @pytest.mark.asyncio
    async def test_changes_produce_unified_diff(self) -> None:
        tool_inst = make_text_diff_tool()
        result = await tool_inst.execute(a="a\nb\nc", b="a\nB\nc")
        assert result.is_error is False
        assert result.data is not None
        assert result.data["changed"] is True
        assert result.data["added"] == 1
        assert result.data["removed"] == 1
        assert "-b" in result.content
        assert "+B" in result.content

    @pytest.mark.asyncio
    async def test_labels_appear_in_headers(self) -> None:
        tool_inst = make_text_diff_tool()
        result = await tool_inst.execute(a="x", b="y", label_a="old.txt", label_b="new.txt")
        assert result.is_error is False
        assert "old.txt" in result.content
        assert "new.txt" in result.content

    @pytest.mark.asyncio
    async def test_pure_addition(self) -> None:
        tool_inst = make_text_diff_tool()
        result = await tool_inst.execute(a="a\nb", b="a\nb\nc")
        assert result.is_error is False
        assert result.data is not None
        assert result.data["added"] == 1
        assert result.data["removed"] == 0

    @pytest.mark.asyncio
    async def test_binary_input_rejected(self) -> None:
        tool_inst = make_text_diff_tool()
        result = await tool_inst.execute(a="ok", b="bad\x00byte")
        assert result.is_error is True
        assert "Binary" in result.content

    @pytest.mark.asyncio
    async def test_oversized_input_rejected(self) -> None:
        tool_inst = make_text_diff_tool()
        result = await tool_inst.execute(a="x" * (_MAX_CHARS + 1), b="y")
        assert result.is_error is True
        assert "too large" in result.content
