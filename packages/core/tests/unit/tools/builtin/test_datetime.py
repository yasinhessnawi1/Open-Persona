"""Tests for the datetime built-in tool (spec 26 T02)."""

from __future__ import annotations

import pytest
from persona.tools.builtin.datetime import make_datetime_tool
from persona.tools.protocol import AsyncTool


class TestDatetimeTool:
    def test_is_async_tool(self) -> None:
        tool_inst = make_datetime_tool()
        assert isinstance(tool_inst, AsyncTool)
        assert tool_inst.name == "datetime"

    @pytest.mark.asyncio
    async def test_now_returns_iso_in_timezone(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(operation="now", timezone="Europe/Oslo")
        assert result.is_error is False
        assert result.data is not None
        assert result.data["timezone"] == "Europe/Oslo"
        assert "Europe/Oslo" in result.content

    @pytest.mark.asyncio
    async def test_now_defaults_to_utc(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(operation="now")
        assert result.is_error is False
        assert result.data is not None
        assert result.data["timezone"] == "UTC"

    @pytest.mark.asyncio
    async def test_unknown_timezone_returns_error(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(operation="now", timezone="Mars/Olympus_Mons")
        assert result.is_error is True
        assert "Unknown timezone" in result.content

    @pytest.mark.asyncio
    async def test_convert_between_timezones(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(
            operation="convert",
            datetime="2026-06-14T12:00:00+00:00",
            to_timezone="America/New_York",
        )
        assert result.is_error is False
        assert result.data is not None
        # UTC noon → 08:00 EDT (UTC-4 in June).
        assert "T08:00:00" in result.data["datetime"]

    @pytest.mark.asyncio
    async def test_convert_naive_uses_base_timezone(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(
            operation="convert",
            datetime="2026-06-14T12:00:00",
            timezone="UTC",
            to_timezone="Europe/Oslo",
        )
        assert result.is_error is False
        assert result.data is not None
        # Naive input assumed UTC → 14:00 CEST (UTC+2 in June).
        assert "T14:00:00" in result.data["datetime"]

    @pytest.mark.asyncio
    async def test_convert_missing_args_returns_error(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(operation="convert", datetime="2026-06-14T12:00:00")
        assert result.is_error is True
        assert "to_timezone" in result.content

    @pytest.mark.asyncio
    async def test_add_days(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(
            operation="add", datetime="2026-06-14T12:00:00+00:00", amount=10, unit="days"
        )
        assert result.is_error is False
        assert result.data is not None
        assert "2026-06-24T12:00:00" in result.data["datetime"]

    @pytest.mark.asyncio
    async def test_add_negative_subtracts(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(
            operation="add", datetime="2026-06-14T12:00:00+00:00", amount=-2, unit="hours"
        )
        assert result.is_error is False
        assert result.data is not None
        assert "2026-06-14T10:00:00" in result.data["datetime"]

    @pytest.mark.asyncio
    async def test_diff_in_seconds_and_days(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(
            operation="diff",
            datetime="2026-06-14T00:00:00+00:00",
            end="2026-06-16T00:00:00+00:00",
        )
        assert result.is_error is False
        assert result.data is not None
        assert result.data["days"] == 2
        assert result.data["total_seconds"] == pytest.approx(172800.0)

    @pytest.mark.asyncio
    async def test_unparseable_datetime_returns_error(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(
            operation="add", datetime="not-a-date", amount=1, unit="days"
        )
        assert result.is_error is True
        assert "Could not parse" in result.content

    @pytest.mark.asyncio
    async def test_invalid_operation_rejected_by_decorator(self) -> None:
        tool_inst = make_datetime_tool()
        result = await tool_inst.execute(operation="frobnicate")
        assert result.is_error is True
