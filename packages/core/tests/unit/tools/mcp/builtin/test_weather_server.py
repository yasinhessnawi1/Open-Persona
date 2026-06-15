"""Unit tests for the built-in ``weather`` MCP server (Spec 27 T9).

HTTP is mocked with ``httpx.MockTransport`` so no network is touched; a single
``external``-marked test exercises the real open-meteo API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from persona.tools.mcp.builtin import SERVER_BUILDERS
from persona.tools.mcp.builtin import weather_server as ws
from persona.tools.mcp.builtin.weather_server import build, fetch_weather

if TYPE_CHECKING:
    from collections.abc import Callable

    _Handler = Callable[[httpx.Request], httpx.Response]

_GEO_OK = {
    "results": [{"latitude": 59.91, "longitude": 10.75, "name": "Oslo", "country": "Norway"}]
}
_FORECAST_OK = {
    "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
    "current": {
        "temperature_2m": 12.3,
        "relative_humidity_2m": 71,
        "weather_code": 3,
        "wind_speed_10m": 9.0,
    },
    "daily": {
        "time": ["2026-06-14"],
        "temperature_2m_max": [18.0],
        "temperature_2m_min": [9.0],
        "weather_code": [61],
    },
}


def _client(handler: _Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if "geocoding-api" in str(request.url):
        return httpx.Response(200, json=_GEO_OK)
    return httpx.Response(200, json=_FORECAST_OK)


@pytest.mark.asyncio
async def test_fetch_weather_formats_current_and_forecast() -> None:
    async with _client(_ok_handler) as client:
        report = await fetch_weather(client, "Oslo", 1)
    assert "Oslo, Norway" in report
    assert "12.3°C" in report
    assert "overcast" in report  # weather_code 3
    assert "slight rain" in report  # daily weather_code 61
    assert "9.0–18.0°C" in report


@pytest.mark.asyncio
async def test_location_not_found_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 — fixed stub response
        return httpx.Response(200, json={"results": []})

    async with _client(handler) as client:
        with pytest.raises(ValueError, match="location not found"):
            await fetch_weather(client, "Atlantis", 1)


@pytest.mark.asyncio
async def test_http_error_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 — fixed stub response
        return httpx.Response(503, text="unavailable")

    async with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_weather(client, "Oslo", 1)


@pytest.mark.asyncio
async def test_days_are_clamped_to_valid_range() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding-api" in str(request.url):
            return httpx.Response(200, json=_GEO_OK)
        captured["forecast_days"] = request.url.params.get("forecast_days")
        return httpx.Response(200, json=_FORECAST_OK)

    async with _client(handler) as client:
        await fetch_weather(client, "Oslo", 999)
    assert captured["forecast_days"] == "7"  # clamped to _MAX_DAYS


@pytest.mark.asyncio
async def test_build_exposes_get_weather_tool() -> None:
    server = build("127.0.0.1", 8500)
    assert server.name == "weather"
    assert "get_weather" in {t.name for t in await server.list_tools()}


@pytest.mark.asyncio
async def test_tool_wraps_failure_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(client: httpx.AsyncClient, location: str, days: int) -> str:  # noqa: ARG001
        raise ValueError("location not found: 'X'")

    monkeypatch.setattr(ws, "fetch_weather", _boom)
    server = build("127.0.0.1", 8501)
    with pytest.raises(Exception, match="weather lookup failed"):
        await server.call_tool("get_weather", {"location": "X"})


def test_weather_is_registered_in_the_builder_registry() -> None:
    assert SERVER_BUILDERS.get("weather") is build


@pytest.mark.external
@pytest.mark.asyncio
async def test_real_open_meteo_call() -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        report = await fetch_weather(client, "Oslo", 1)
    assert "Oslo" in report
