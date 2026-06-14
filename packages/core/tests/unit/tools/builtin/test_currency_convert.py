"""Tests for the currency_convert built-in tool (spec 26 T04).

Frankfurter (no key) is the default provider; httpx is mocked via
MockTransport so no network is touched.
"""

# ruff: noqa: ANN401, ARG001
from __future__ import annotations

from typing import Any

import httpx
import pytest
from persona.tools.builtin.currency_convert import make_currency_convert_tool
from persona.tools.protocol import AsyncTool


def _mock_http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


class TestCurrencyConvertTool:
    def test_is_async_tool(self) -> None:
        tool_inst = make_currency_convert_tool()
        assert isinstance(tool_inst, AsyncTool)
        assert tool_inst.name == "currency_convert"

    @pytest.mark.asyncio
    async def test_frankfurter_happy_path(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "api.frankfurter.dev"
            assert request.url.params["base"] == "EUR"
            assert request.url.params["symbols"] == "NOK"
            return httpx.Response(
                200,
                json={"amount": 1.0, "base": "EUR", "date": "2026-06-13", "rates": {"NOK": 11.66}},
            )

        async with _mock_http(handler) as client:
            tool_inst = make_currency_convert_tool(provider_name="frankfurter", http=client)
            result = await tool_inst.execute(amount=50, from_currency="eur", to_currency="nok")

        assert result.is_error is False
        assert result.data is not None
        assert result.data["converted"] == pytest.approx(583.0)
        assert result.data["rate"] == pytest.approx(11.66)
        assert "NOK" in result.content

    @pytest.mark.asyncio
    async def test_exchangerate_api_happy_path(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "open.er-api.com"
            assert request.url.path.endswith("/USD")
            return httpx.Response(
                200,
                json={
                    "result": "success",
                    "base_code": "USD",
                    "time_last_update_utc": "Sat, 13 Jun 2026 00:00:01 +0000",
                    "rates": {"EUR": 0.92},
                },
            )

        async with _mock_http(handler) as client:
            tool_inst = make_currency_convert_tool(provider_name="exchangerate_api", http=client)
            result = await tool_inst.execute(amount=10, from_currency="USD", to_currency="EUR")

        assert result.is_error is False
        assert result.data is not None
        assert result.data["converted"] == pytest.approx(9.2)

    @pytest.mark.asyncio
    async def test_same_currency_shortcuts_without_network(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
            raise AssertionError("network should not be called for same-currency")

        async with _mock_http(handler) as client:
            tool_inst = make_currency_convert_tool(http=client)
            result = await tool_inst.execute(amount=5, from_currency="USD", to_currency="USD")

        assert result.is_error is False
        assert result.data is not None
        assert result.data["rate"] == 1.0

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self) -> None:
        tool_inst = make_currency_convert_tool(provider_name="bogus")
        result = await tool_inst.execute(amount=1, from_currency="USD", to_currency="EUR")
        assert result.is_error is True
        assert "Unknown currency provider" in result.content

    @pytest.mark.asyncio
    async def test_invalid_currency_code_returns_error(self) -> None:
        tool_inst = make_currency_convert_tool()
        result = await tool_inst.execute(amount=1, from_currency="DOLLARS", to_currency="EUR")
        assert result.is_error is True
        assert "ISO 4217" in result.content

    @pytest.mark.asyncio
    async def test_unknown_code_in_response_returns_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"amount": 1.0, "base": "EUR", "rates": {}})

        async with _mock_http(handler) as client:
            tool_inst = make_currency_convert_tool(http=client)
            result = await tool_inst.execute(amount=1, from_currency="EUR", to_currency="XYZ")

        assert result.is_error is True
        assert "rate" in result.content.lower()

    @pytest.mark.asyncio
    async def test_rate_limit_returns_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="slow down")

        async with _mock_http(handler) as client:
            tool_inst = make_currency_convert_tool(http=client)
            result = await tool_inst.execute(amount=1, from_currency="EUR", to_currency="USD")

        assert result.is_error is True
        assert "429" in result.content

    @pytest.mark.asyncio
    async def test_network_error_returns_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        async with _mock_http(handler) as client:
            tool_inst = make_currency_convert_tool(http=client)
            result = await tool_inst.execute(amount=1, from_currency="EUR", to_currency="USD")

        assert result.is_error is True
        assert "Network error" in result.content

    @pytest.mark.asyncio
    async def test_malformed_json_returns_error_not_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")

        async with _mock_http(handler) as client:
            tool_inst = make_currency_convert_tool(http=client)
            result = await tool_inst.execute(amount=1, from_currency="EUR", to_currency="USD")

        assert result.is_error is True
