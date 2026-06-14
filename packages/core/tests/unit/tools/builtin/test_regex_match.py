"""Tests for the regex_match built-in tool (spec 26 T03).

Uses ``google-re2`` (ReDoS-immune linear-time matching) per
D-26-X-regex-re2-undefer — patterns are model-supplied and untrusted.
"""

from __future__ import annotations

import pytest
from persona.tools.builtin.regex_match import _MAX_TEXT_LEN, make_regex_match_tool
from persona.tools.protocol import AsyncTool


class TestRegexMatchTool:
    def test_is_async_tool(self) -> None:
        tool_inst = make_regex_match_tool()
        assert isinstance(tool_inst, AsyncTool)
        assert tool_inst.name == "regex_match"

    @pytest.mark.asyncio
    async def test_search_finds_match_with_groups(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern=r"(\d+)-(\d+)", text="order 12-34 ok")
        assert result.is_error is False
        assert result.data == {
            "matched": True,
            "match": "12-34",
            "groups": ["12", "34"],
            "span": [6, 11],
        }

    @pytest.mark.asyncio
    async def test_search_no_match(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern=r"\d+", text="no digits here")
        assert result.is_error is False
        assert result.data == {"matched": False}

    @pytest.mark.asyncio
    async def test_ignore_case(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(
            pattern="hello", text="HELLO world", operation="search", ignore_case=True
        )
        assert result.is_error is False
        assert result.data is not None
        assert result.data["matched"] is True

    @pytest.mark.asyncio
    async def test_fullmatch_true(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern=r"\d{3}", text="123", operation="fullmatch")
        assert result.is_error is False
        assert result.data is not None
        assert result.data["matched"] is True

    @pytest.mark.asyncio
    async def test_fullmatch_false_on_partial(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern=r"\d{3}", text="123abc", operation="fullmatch")
        assert result.is_error is False
        assert result.data is not None
        assert result.data["matched"] is False

    @pytest.mark.asyncio
    async def test_findall(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern=r"\d+", text="a1b22c333", operation="findall")
        assert result.is_error is False
        assert result.data == {"count": 3, "matches": ["1", "22", "333"]}

    @pytest.mark.asyncio
    async def test_sub_replaces(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(
            pattern=r"\d+", text="a1b22", operation="sub", replacement="#"
        )
        assert result.is_error is False
        assert result.content == "a#b#"
        assert result.data == {"result": "a#b#", "replacements": 2}

    @pytest.mark.asyncio
    async def test_sub_without_replacement_errors(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern=r"\d+", text="a1", operation="sub")
        assert result.is_error is True
        assert "replacement" in result.content

    @pytest.mark.asyncio
    async def test_invalid_pattern_returns_error_not_raises(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern=r"(unbalanced", text="x")
        assert result.is_error is True
        assert "Invalid regular expression" in result.content

    @pytest.mark.asyncio
    async def test_backreference_rejected_cleanly(self) -> None:
        # RE2 has no backreferences — this must be a clean error, not a crash.
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern=r"(a)\1", text="aa")
        assert result.is_error is True
        assert "backreferences or lookaround" in result.content

    @pytest.mark.asyncio
    async def test_redos_pattern_completes_quickly(self) -> None:
        # The classic catastrophic-backtracking pattern + adversarial input.
        # Under stdlib ``re`` this hangs; RE2 returns in linear time.
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern=r"(a+)+$", text="a" * 40 + "!", operation="search")
        assert result.is_error is False
        assert result.data is not None
        assert result.data["matched"] is False

    @pytest.mark.asyncio
    async def test_oversized_text_rejected(self) -> None:
        tool_inst = make_regex_match_tool()
        result = await tool_inst.execute(pattern="x", text="a" * (_MAX_TEXT_LEN + 1))
        assert result.is_error is True
        assert "too long" in result.content
