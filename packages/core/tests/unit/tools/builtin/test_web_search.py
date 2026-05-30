"""Tests for the web_search built-in tool (T07)."""

# ruff: noqa: ANN401, ARG001, ARG002, ERA001
from __future__ import annotations

from typing import Any

import httpx
import pytest
from persona.tools.builtin._search_providers import (
    BraveSearchProvider,
    SerpAPISearchProvider,
    TavilySearchProvider,
)
from persona.tools.builtin.web_search import make_web_search_tool
from persona.tools.protocol import AsyncTool

# Section: helpers


def _mock_brave_response(results: list[dict[str, str]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"web": {"results": results}},
    )


def _make_mock_http(handler: Any) -> httpx.AsyncClient:
    """An AsyncClient whose requests run through ``handler`` (a MockTransport)."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0))


# Section: Brave happy path


class TestBraveHappyPath:
    @pytest.mark.asyncio
    async def test_returns_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "api.search.brave.com"
            assert request.headers["X-Subscription-Token"] == "test-key"
            return _mock_brave_response(
                [
                    {"title": "T1", "url": "https://x/1", "description": "S1"},
                    {"title": "T2", "url": "https://x/2", "description": "S2"},
                ]
            )

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(
                provider_name="brave",
                api_key="test-key",
                http=client,
            )
            result = await tool_inst.execute(query="norway tenancy law", max_results=5)

        assert result.is_error is False
        assert "T1" in result.content
        assert "https://x/2" in result.content
        assert result.data is not None
        assert len(result.data["results"]) == 2
        assert result.data["results"][0]["title"] == "T1"
        assert result.data["provider"] == "brave"

    @pytest.mark.asyncio
    async def test_respects_max_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # 3 results returned even though we asked for 2.
            return _mock_brave_response(
                [
                    {"title": f"T{i}", "url": f"https://x/{i}", "description": f"S{i}"}
                    for i in range(1, 4)
                ]
            )

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(provider_name="brave", api_key="k", http=client)
            result = await tool_inst.execute(query="q", max_results=2)
        assert result.data is not None
        assert len(result.data["results"]) == 2

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _mock_brave_response([])

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(provider_name="brave", api_key="k", http=client)
            result = await tool_inst.execute(query="zxcv-no-hits")
        assert result.is_error is False
        assert "No results" in result.content
        assert result.data is not None
        assert result.data["results"] == []


# Section: error mapping


class TestBraveErrorMapping:
    @pytest.mark.asyncio
    async def test_401_returns_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid_key"})

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(provider_name="brave", api_key="bad", http=client)
            result = await tool_inst.execute(query="x")
        assert result.is_error is True
        assert "Authentication" in result.content or "401" in result.content
        assert "PERSONA_WEB_SEARCH_API_KEY" in result.content

    @pytest.mark.asyncio
    async def test_429_returns_rate_limit(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate_limit"})

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(provider_name="brave", api_key="k", http=client)
            result = await tool_inst.execute(query="x")
        assert result.is_error is True
        assert "Rate limit" in result.content or "429" in result.content

    @pytest.mark.asyncio
    async def test_500_returns_generic_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server is on fire")

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(provider_name="brave", api_key="k", http=client)
            result = await tool_inst.execute(query="x")
        assert result.is_error is True
        assert "500" in result.content

    @pytest.mark.asyncio
    async def test_network_error_returns_error_result(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns failed")

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(provider_name="brave", api_key="k", http=client)
            result = await tool_inst.execute(query="x")
        assert result.is_error is True
        # The body's ConnectError leaks past httpx.HTTPStatusError handler;
        # our HTTPError catch handles it.
        assert "ConnectError" in result.content or "Network" in result.content


# Section: configuration errors


class TestConfigurationErrors:
    @pytest.mark.asyncio
    async def test_missing_api_key_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PERSONA_WEB_SEARCH_API_KEY", raising=False)
        tool_inst = make_web_search_tool(provider_name="brave")  # no api_key, no env
        result = await tool_inst.execute(query="x")
        assert result.is_error is True
        assert "PERSONA_WEB_SEARCH_API_KEY" in result.content

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self) -> None:
        tool_inst = make_web_search_tool(provider_name="bogus", api_key="k")
        result = await tool_inst.execute(query="x")
        assert result.is_error is True
        assert "Unknown search provider" in result.content
        assert "brave" in result.content  # lists supported

    @pytest.mark.asyncio
    async def test_tavily_serves_results(self) -> None:
        # Tavily is now a real provider (spec 11, T07 launch follow-up). The
        # tool envelope returns ToolResult with the summarised content rows.
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "api.tavily.com"
            assert request.method == "POST"
            assert request.headers["Authorization"] == "Bearer tvly-test"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "T1", "url": "https://x/1", "content": "summarised one"},
                        {"title": "T2", "url": "https://x/2", "content": "summarised two"},
                    ],
                },
            )

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(
                provider_name="tavily", api_key="tvly-test", http=client
            )
            result = await tool_inst.execute(query="norway tenancy law", max_results=5)

        assert result.is_error is False
        assert "T1" in result.content
        assert "https://x/2" in result.content
        assert result.data is not None
        assert len(result.data["results"]) == 2
        assert result.data["results"][0]["title"] == "T1"
        assert result.data["results"][0]["snippet"] == "summarised one"
        assert result.data["provider"] == "tavily"

    @pytest.mark.asyncio
    async def test_serpapi_stub_returns_error(self) -> None:
        async with _make_mock_http(lambda _req: httpx.Response(200)) as client:
            tool_inst = make_web_search_tool(provider_name="serpapi", api_key="k", http=client)
            result = await tool_inst.execute(query="x")
        assert result.is_error is True
        assert "NotImplementedError" in result.content


# Section: env-var defaults


class TestEnvVarDefaults:
    @pytest.mark.asyncio
    async def test_env_var_provider_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_WEB_SEARCH_PROVIDER", "brave")
        monkeypatch.setenv("PERSONA_WEB_SEARCH_API_KEY", "env-key")

        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["host"] = request.url.host
            captured["token"] = request.headers["X-Subscription-Token"]
            return _mock_brave_response([])

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(http=client)  # no explicit provider/key
            await tool_inst.execute(query="x")
        assert captured["host"] == "api.search.brave.com"
        assert captured["token"] == "env-key"

    @pytest.mark.asyncio
    async def test_explicit_args_override_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_WEB_SEARCH_API_KEY", "env-key")

        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["token"] = request.headers["X-Subscription-Token"]
            return _mock_brave_response([])

        async with _make_mock_http(handler) as client:
            tool_inst = make_web_search_tool(api_key="explicit-key", http=client)
            await tool_inst.execute(query="x")
        assert captured["token"] == "explicit-key"


# Section: AsyncTool conformance


class TestAsyncToolConformance:
    def test_satisfies_async_tool(self) -> None:
        tool_inst = make_web_search_tool(provider_name="brave", api_key="k")
        assert isinstance(tool_inst, AsyncTool)
        assert tool_inst.name == "web_search"
        assert "title" in tool_inst.description.lower() or "search" in tool_inst.description.lower()
        assert "query" in tool_inst.parameters_schema["properties"]


# Section: provider classes (direct)


class TestProviderClasses:
    """Direct unit tests on the provider implementations (D-03-9)."""

    @pytest.mark.asyncio
    async def test_brave_satisfies_protocol(self) -> None:
        from persona.tools.builtin._search_providers import _SearchProvider

        async with httpx.AsyncClient() as client:
            assert isinstance(BraveSearchProvider("k", client), _SearchProvider)

    @pytest.mark.asyncio
    async def test_tavily_satisfies_protocol(self) -> None:
        from persona.tools.builtin._search_providers import _SearchProvider

        async with httpx.AsyncClient() as client:
            assert isinstance(TavilySearchProvider("k", client), _SearchProvider)

    @pytest.mark.asyncio
    async def test_tavily_clamps_max_results(self) -> None:
        # Tavily caps at 20 per request; the provider must clamp before sending.
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(200, json={"results": []})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            p = TavilySearchProvider("k", client)
            await p.search("query", max_results=100)
        import json

        body = json.loads(captured["body"])
        assert body["max_results"] == 20  # clamped
        assert body["query"] == "query"
        assert body["search_depth"] == "basic"  # not the 2-credit advanced mode

    @pytest.mark.asyncio
    async def test_serpapi_stub_raises(self) -> None:
        async with httpx.AsyncClient() as client:
            p = SerpAPISearchProvider("k", client)
            with pytest.raises(NotImplementedError, match="SerpAPI"):
                await p.search("x", 5)
