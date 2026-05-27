"""Tests for ``OllamaBackend`` — mocks ``httpx.AsyncClient`` end to end."""

# ruff: noqa: ANN401, SLF001 — mocks use Any; tests poke private state

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from persona.backends.config import BackendConfig
from persona.backends.errors import (
    AuthenticationError,
    BackendTimeoutError,
    ModelNotFoundError,
    ProviderError,
    RateLimitError,
)
from persona.backends.ollama import OllamaBackend
from persona.backends.protocol import ChatBackend
from persona.backends.types import StreamChunk, ToolSpec
from persona.schema.conversation import ConversationMessage
from pydantic import SecretStr


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=text, created_at=datetime.now(UTC))


def _config(*, model: str = "llama3", api_key: str | None = None) -> BackendConfig:
    return BackendConfig(
        provider="ollama",
        model=model,
        api_key=SecretStr(api_key) if api_key else None,
    )


# -----------------------------------------------------------------------------
# Construction
# -----------------------------------------------------------------------------


class TestConstruction:
    def test_constructs_without_health_check(self) -> None:
        # D-02-9 — no network call at __init__.
        backend = OllamaBackend(_config())
        assert backend.provider_name == "ollama"
        assert backend.model_name == "llama3"
        assert backend.supports_native_tools is False
        assert backend._client is None  # lazy

    def test_native_tools_opt_in(self) -> None:
        backend = OllamaBackend(_config(), use_native_tools=True)
        assert backend.supports_native_tools is True

    def test_implements_chat_backend_protocol(self) -> None:
        assert isinstance(OllamaBackend(_config()), ChatBackend)

    def test_wrong_provider_raises(self) -> None:
        config = BackendConfig(provider="openai", model="x", api_key=SecretStr("k"))
        with pytest.raises(ProviderError):
            OllamaBackend(config)

    def test_custom_base_url(self) -> None:
        config = BackendConfig(
            provider="ollama",
            model="llama3",
            base_url="http://remote.example:11434",
        )
        backend = OllamaBackend(config)
        assert backend._base_url == "http://remote.example:11434"


# -----------------------------------------------------------------------------
# Mock helpers
# -----------------------------------------------------------------------------


def _mock_response(
    *,
    status: int = 200,
    json_body: dict[str, Any] | None = None,
    content_type: str = "application/json",
) -> Any:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.is_success = 200 <= status < 300
    response.headers = {"content-type": content_type}
    response.json = MagicMock(return_value=json_body or {})
    return response


def _ndjson_lines(*chunks: dict[str, Any]) -> list[str]:
    return [json.dumps(c) for c in chunks]


class _FakeStreamContext:
    """Mimics the async context manager returned by ``httpx.AsyncClient.stream``."""

    def __init__(self, lines: list[str], *, status: int = 200) -> None:
        self._lines = lines
        self._status = status
        self.response = MagicMock(spec=httpx.Response)
        self.response.status_code = status
        self.response.is_success = 200 <= status < 300
        self.response.headers = {"content-type": "application/x-ndjson"}
        self.response.json = MagicMock(return_value={})

    async def __aenter__(self) -> Any:
        return self.response

    async def __aexit__(self, *args: Any) -> None:
        return None


def _patch_stream(backend: OllamaBackend, lines: list[str], *, status: int = 200) -> Any:
    """Patch the lazy ``_client.stream`` to return our fake context."""
    fake_ctx = _FakeStreamContext(lines, status=status)

    async def aiter_lines() -> Any:
        for line in lines:
            yield line

    fake_ctx.response.aiter_lines = aiter_lines  # type: ignore[method-assign]
    client = MagicMock()
    client.stream = MagicMock(return_value=fake_ctx)
    client.post = AsyncMock()
    backend._client = client
    return client


# -----------------------------------------------------------------------------
# Section: single-shot chat
# -----------------------------------------------------------------------------


class TestChat:
    @pytest.mark.asyncio
    async def test_chat_returns_response(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(
                json_body={
                    "model": "llama3",
                    "message": {"role": "assistant", "content": "hello"},
                    "done": True,
                    "prompt_eval_count": 5,
                    "eval_count": 3,
                }
            )
        )
        backend._client = client
        response = await backend.chat([_user("hi")])
        assert response.content == "hello"
        assert response.provider == "ollama"
        assert response.usage.prompt_tokens == 5
        assert response.usage.completion_tokens == 3

    @pytest.mark.asyncio
    async def test_chat_with_native_tool_call(self) -> None:
        backend = OllamaBackend(_config(), use_native_tools=True)
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(
                json_body={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "arguments": {"q": "kittens"},
                                },
                            }
                        ],
                    },
                    "done": True,
                }
            )
        )
        backend._client = client
        response = await backend.chat(
            [_user("search")],
            tools=[ToolSpec(name="web_search", description="x", parameters={})],
        )
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "web_search"
        assert response.tool_calls[0].args == {"q": "kittens"}

    @pytest.mark.asyncio
    async def test_chat_string_arguments_get_parsed(self) -> None:
        backend = OllamaBackend(_config(), use_native_tools=True)
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(
                json_body={
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"q":"k"}',
                                }
                            }
                        ],
                    },
                    "done": True,
                }
            )
        )
        backend._client = client
        response = await backend.chat([_user("x")])
        assert response.tool_calls[0].args == {"q": "k"}

    @pytest.mark.asyncio
    async def test_chat_with_shim_tools(self) -> None:
        backend = OllamaBackend(_config(), use_native_tools=False)
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(
                json_body={
                    "message": {
                        "role": "assistant",
                        "content": (
                            'searching now {"tool": "web_search", "args": {"q": "kittens"}}'
                        ),
                    },
                    "done": True,
                }
            )
        )
        backend._client = client
        response = await backend.chat(
            [_user("search")],
            tools=[ToolSpec(name="web_search", description="x", parameters={})],
        )
        assert response.tool_calls[0].name == "web_search"
        assert response.tool_calls[0].args == {"q": "kittens"}
        assert "searching now" in response.content


# -----------------------------------------------------------------------------
# Section: streaming chat
# -----------------------------------------------------------------------------


class TestChatStream:
    @pytest.mark.asyncio
    async def test_stream_yields_text_and_final(self) -> None:
        backend = OllamaBackend(_config())
        lines = _ndjson_lines(
            {"message": {"content": "Hel"}, "done": False},
            {"message": {"content": "lo"}, "done": False},
            {"message": {"content": ""}, "done": True, "prompt_eval_count": 4, "eval_count": 2},
        )
        _patch_stream(backend, lines)
        collected: list[StreamChunk] = []
        async for chunk in backend.chat_stream([_user("hi")]):
            collected.append(chunk)
        text_chunks = [c for c in collected if c.delta and not c.is_final]
        finals = [c for c in collected if c.is_final]
        assert len(text_chunks) == 2
        assert len(finals) == 1
        assert finals[0].usage is not None
        assert finals[0].usage.total_tokens == 6

    @pytest.mark.asyncio
    async def test_stream_with_native_tool_call_emits_delta(self) -> None:
        backend = OllamaBackend(_config(), use_native_tools=True)
        lines = _ndjson_lines(
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "web_search", "arguments": {"q": "x"}}}],
                },
                "done": False,
            },
            {"message": {"content": ""}, "done": True},
        )
        _patch_stream(backend, lines)
        collected: list[StreamChunk] = []
        async for chunk in backend.chat_stream(
            [_user("search")],
            tools=[ToolSpec(name="web_search", description="x", parameters={})],
        ):
            collected.append(chunk)
        tool_deltas = [c for c in collected if c.tool_call_delta is not None]
        assert len(tool_deltas) == 1
        assert tool_deltas[0].tool_call_delta is not None
        assert tool_deltas[0].tool_call_delta.name_delta == "web_search"


# -----------------------------------------------------------------------------
# Error mapping
# -----------------------------------------------------------------------------


class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_404_model_not_found(self) -> None:
        backend = OllamaBackend(_config(model="imagined"))
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(
                status=404,
                json_body={"error": "model 'imagined' not found"},
            )
        )
        backend._client = client
        with pytest.raises(ModelNotFoundError) as info:
            await backend.chat([_user("x")])
        assert "model=imagined" in str(info.value)

    @pytest.mark.asyncio
    async def test_401_authentication(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(status=401, json_body={"error": "unauthorized"})
        )
        backend._client = client
        with pytest.raises(AuthenticationError):
            await backend.chat([_user("x")])

    @pytest.mark.asyncio
    async def test_429_rate_limit(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        response_mock = _mock_response(status=429, json_body={"error": "slow down"})
        response_mock.headers = {
            "content-type": "application/json",
            "retry-after": "10",
        }
        client.post = AsyncMock(return_value=response_mock)
        backend._client = client
        with pytest.raises(RateLimitError) as info:
            await backend.chat([_user("x")])
        assert "retry_after_s=10" in str(info.value)

    @pytest.mark.asyncio
    async def test_timeout_to_backend_timeout_error(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.ReadTimeout("read timeout"))
        backend._client = client
        with pytest.raises(BackendTimeoutError):
            await backend.chat([_user("x")])

    @pytest.mark.asyncio
    async def test_500_provider_error(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_mock_response(status=500, json_body={"error": "boom"})
        )
        backend._client = client
        with pytest.raises(ProviderError):
            await backend.chat([_user("x")])

    @pytest.mark.asyncio
    async def test_connect_error_to_provider_error(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        backend._client = client
        with pytest.raises(ProviderError) as info:
            await backend.chat([_user("x")])
        assert "connection" in str(info.value)


# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_aclose_closes_client_and_idempotent(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        client.aclose = AsyncMock()
        backend._client = client
        await backend.aclose()
        client.aclose.assert_awaited_once()
        # Second call: noop.
        await backend.aclose()
        client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ping_returns_false_on_http_error(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        backend._client = client
        assert await backend.ping() is False

    @pytest.mark.asyncio
    async def test_ping_returns_true_on_success(self) -> None:
        backend = OllamaBackend(_config())
        client = MagicMock()
        success = MagicMock()
        success.is_success = True
        client.get = AsyncMock(return_value=success)
        backend._client = client
        assert await backend.ping() is True
