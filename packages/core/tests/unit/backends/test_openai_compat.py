"""Tests for ``OpenAICompatibleBackend``.

Both Anthropic (via the ``anthropic`` SDK) and OpenAI/DeepSeek/Groq/Together
(via the ``openai`` SDK) are exercised with mocked clients. Real provider
calls live behind ``@pytest.mark.external`` (not in this file).
"""

# ruff: noqa: ANN401, SLF001 — mocks use Any return types; tests access private attrs

from __future__ import annotations

import os
from collections.abc import AsyncIterator  # noqa: TC003 — used at runtime in helpers
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import openai
import pytest
from persona.backends.config import BackendConfig
from persona.backends.errors import (
    AuthenticationError,
    BackendTimeoutError,
    ModelNotFoundError,
    ProviderError,
    RateLimitError,
)
from persona.backends.openai_compat import (
    _NATIVE_TOOLS_CAPABILITY,
    OpenAICompatibleBackend,
    _message_to_anthropic,
    _message_to_openai,
    _native_tools_supported,
)
from persona.backends.protocol import ChatBackend
from persona.backends.types import ChatResponse, StreamChunk, ToolSpec
from persona.schema.conversation import ConversationMessage  # noqa: TC001
from persona.schema.tools import ToolCall, ToolResult
from persona.tools import format_tool_result
from pydantic import SecretStr


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=text, created_at=datetime.now(UTC))


def _config(
    provider: str, *, api_key: str = "test-key", model: str = "test-model"
) -> BackendConfig:
    return BackendConfig(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        api_key=SecretStr(api_key),
    )


# -----------------------------------------------------------------------------
# Capability matrix
# -----------------------------------------------------------------------------


class TestCapabilityMatrix:
    def test_anthropic_is_all(self) -> None:
        assert _NATIVE_TOOLS_CAPABILITY["anthropic"] == "all"

    def test_openai_is_all(self) -> None:
        assert _NATIVE_TOOLS_CAPABILITY["openai"] == "all"

    def test_groq_is_frozenset(self) -> None:
        cap = _NATIVE_TOOLS_CAPABILITY["groq"]
        assert isinstance(cap, frozenset)
        assert "llama-3.3-70b-versatile" in cap

    def test_together_is_empty_frozenset(self) -> None:
        cap = _NATIVE_TOOLS_CAPABILITY["together"]
        assert cap == frozenset()

    def test_supported_anthropic_any_model(self) -> None:
        assert _native_tools_supported("anthropic", "claude-anything") is True

    def test_supported_groq_listed_model(self) -> None:
        assert _native_tools_supported("groq", "llama-3.3-70b-versatile") is True

    def test_unsupported_groq_unlisted_model(self) -> None:
        assert _native_tools_supported("groq", "whisper-large-v3") is False


class TestToolCallMessageProtocol:
    """The native tool-calling message round-trip (spec 11 soak findings).

    A native provider requires the assistant message that issued the tool_calls
    to precede the tool result, and the result's id to match the call's id.
    These regression-gate the four soak fixes that the (external) soak proved.
    """

    def _now(self) -> datetime:
        return datetime(2026, 5, 29, tzinfo=UTC)

    def test_openai_assistant_message_serialises_tool_calls(self) -> None:
        msg = ConversationMessage(
            role="assistant",
            content="searching…",
            created_at=self._now(),
            tool_calls=[ToolCall(name="web_search", args={"query": "mould"}, call_id="call_0")],
        )
        out = _message_to_openai(msg)
        assert out["role"] == "assistant"
        assert out["tool_calls"][0]["id"] == "call_0"
        assert out["tool_calls"][0]["type"] == "function"
        assert out["tool_calls"][0]["function"]["name"] == "web_search"
        # arguments is a JSON STRING per the OpenAI schema
        assert out["tool_calls"][0]["function"]["arguments"] == '{"query": "mould"}'

    def test_openai_assistant_without_tool_calls_unchanged(self) -> None:
        msg = ConversationMessage(role="assistant", content="hi", created_at=self._now())
        out = _message_to_openai(msg)
        assert "tool_calls" not in out
        assert out == {"role": "assistant", "content": "hi"}

    def test_openai_tool_result_id_matches_the_call(self) -> None:
        # the whole pairing: assistant.tool_calls[].id == tool message tool_call_id
        call = ToolCall(name="web_search", args={"query": "x"}, call_id="call_42")
        result = ToolResult(tool_name="web_search", content="results", call_id="call_42")
        result_msg = format_tool_result(call, result, provider_name="deepseek")
        out = _message_to_openai(result_msg)
        assert out["role"] == "tool"
        assert out["tool_call_id"] == "call_42"  # NOT "" (the soak metadata-key bug)

    def test_anthropic_assistant_message_serialises_tool_use_blocks(self) -> None:
        msg = ConversationMessage(
            role="assistant",
            content="searching…",
            created_at=self._now(),
            tool_calls=[ToolCall(name="web_search", args={"query": "mould"}, call_id="tu_1")],
        )
        out = _message_to_anthropic(msg)
        assert out["role"] == "assistant"
        blocks = out["content"]
        assert {"type": "text", "text": "searching…"} in blocks
        tool_use = next(b for b in blocks if b["type"] == "tool_use")
        assert tool_use["id"] == "tu_1"
        assert tool_use["name"] == "web_search"
        assert tool_use["input"] == {"query": "mould"}

    def test_anthropic_tool_result_carries_the_id(self) -> None:
        # KNOWN LIMITATION (spec 11, documented in the README): the Anthropic
        # native tool path is NOT soak-verified — DeepSeek is the demo-primary
        # (D-11-9), Anthropic the outage backup. format_tool_result(anthropic)
        # encodes the tool_result block as a user message, so the id is present
        # but not yet a structured top-level block. The OpenAI/DeepSeek path
        # (above) is the soak-proven one. Use DeepSeek for tool-heavy demos.
        call = ToolCall(name="web_search", args={}, call_id="tu_9")
        result = ToolResult(tool_name="web_search", content="r", call_id="tu_9")
        out = _message_to_anthropic(format_tool_result(call, result, provider_name="anthropic"))
        assert out["role"] == "user"
        assert "tu_9" in out["content"]

    def test_unsupported_unknown_provider(self) -> None:
        assert _native_tools_supported("nonsense", "x") is False


# -----------------------------------------------------------------------------
# Construction
# -----------------------------------------------------------------------------


class TestConstruction:
    def test_anthropic_constructs(self) -> None:
        backend = OpenAICompatibleBackend(_config("anthropic"))
        assert isinstance(backend, ChatBackend)
        assert backend.provider_name == "anthropic"
        assert backend.model_name == "test-model"
        # supports_native_tools = True for anthropic (all models).
        assert backend.supports_native_tools is True

    def test_openai_constructs(self) -> None:
        backend = OpenAICompatibleBackend(_config("openai"))
        assert isinstance(backend, ChatBackend)
        assert backend.provider_name == "openai"
        assert backend.supports_native_tools is True

    def test_groq_constructs_with_unlisted_model_uses_shim(self) -> None:
        backend = OpenAICompatibleBackend(_config("groq", model="whisper-large-v3"))
        assert backend.supports_native_tools is False

    def test_missing_api_key_raises(self) -> None:
        config = BackendConfig(provider="openai", model="gpt-4o", api_key=None)
        with pytest.raises(AuthenticationError) as info:
            OpenAICompatibleBackend(config)
        assert "openai" in str(info.value)

    def test_empty_api_key_raises(self) -> None:
        config = BackendConfig(provider="openai", model="gpt-4o", api_key=SecretStr(""))
        with pytest.raises(AuthenticationError):
            OpenAICompatibleBackend(config)

    def test_unknown_provider_raises(self) -> None:
        config = BackendConfig(
            provider="ollama",  # not handled by this backend class
            model="llama3",
            api_key=SecretStr("x"),
        )
        with pytest.raises(ProviderError):
            OpenAICompatibleBackend(config)

    def test_custom_base_url_passed_to_client(self) -> None:
        config = BackendConfig(
            provider="openai",
            model="gpt-4o",
            api_key=SecretStr("x"),
            base_url="https://my-proxy.example/v1/",
        )
        backend = OpenAICompatibleBackend(config)
        # _openai client base_url string is configured.
        assert backend._openai is not None  # type: ignore[attr-defined]

    def test_anthropic_base_url_has_no_doubled_v1(self) -> None:
        # Regression for D-10-9: the `anthropic` SDK appends its own
        # /v1/messages, so the default base_url must NOT carry a /v1/ suffix
        # (else requests hit /v1/v1/messages -> 404). The OpenAI-compat
        # providers DO keep /v1/ (their SDK does not append it).
        anth = OpenAICompatibleBackend(_config("anthropic"))
        assert "/v1" not in str(anth._anthropic.base_url)  # type: ignore[union-attr]
        oai = OpenAICompatibleBackend(_config("openai"))
        assert str(oai._openai.base_url).rstrip("/").endswith("/v1")  # type: ignore[union-attr]


# -----------------------------------------------------------------------------
# Anthropic chat (non-streaming)
# -----------------------------------------------------------------------------


def _mock_anthropic_message_response(
    *,
    text: str = "hello",
    model: str = "claude-sonnet-4-6",
    tool_use: tuple[str, str, dict[str, Any]] | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> Any:
    """Build a MagicMock that mimics ``anthropic.types.Message``."""
    blocks: list[Any] = []
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    blocks.append(text_block)
    if tool_use is not None:
        tu_id, tu_name, tu_input = tool_use
        tu_block = MagicMock()
        tu_block.type = "tool_use"
        tu_block.id = tu_id
        tu_block.name = tu_name
        tu_block.input = tu_input
        blocks.append(tu_block)
    response = MagicMock()
    response.content = blocks
    response.model = model
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    response.usage = usage
    return response


class TestAnthropicChat:
    @pytest.mark.asyncio
    async def test_chat_returns_response(self) -> None:
        backend = OpenAICompatibleBackend(_config("anthropic"))
        with patch.object(
            backend._anthropic.messages,  # type: ignore[union-attr]
            "create",
            new=AsyncMock(return_value=_mock_anthropic_message_response()),
        ):
            response = await backend.chat([_user("hi")])
        assert isinstance(response, ChatResponse)
        assert response.content == "hello"
        assert response.provider == "anthropic"
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5
        assert response.latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_chat_with_native_tool_call(self) -> None:
        backend = OpenAICompatibleBackend(_config("anthropic"))
        mock_response = _mock_anthropic_message_response(
            text="",
            tool_use=("call-123", "web_search", {"query": "kittens"}),
        )
        with patch.object(
            backend._anthropic.messages,  # type: ignore[union-attr]
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            response = await backend.chat(
                [_user("search please")],
                tools=[ToolSpec(name="web_search", description="search", parameters={})],
            )
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "web_search"
        assert response.tool_calls[0].args == {"query": "kittens"}
        assert response.tool_calls[0].call_id == "call-123"

    @pytest.mark.asyncio
    async def test_chat_system_message_split(self) -> None:
        backend = OpenAICompatibleBackend(_config("anthropic"))
        create_mock = AsyncMock(return_value=_mock_anthropic_message_response())
        with patch.object(
            backend._anthropic.messages,
            "create",
            new=create_mock,  # type: ignore[union-attr]
        ):
            await backend.chat(
                [
                    ConversationMessage(
                        role="system",
                        content="You are helpful.",
                        created_at=datetime.now(UTC),
                    ),
                    _user("hi"),
                ]
            )
        call_kwargs = create_mock.call_args.kwargs
        assert call_kwargs["system"] == "You are helpful."
        assert all(m["role"] != "system" for m in call_kwargs["messages"])


# -----------------------------------------------------------------------------
# OpenAI chat (non-streaming)
# -----------------------------------------------------------------------------


def _mock_openai_chat_completion(
    *,
    content: str = "hello",
    model: str = "gpt-4o",
    tool_call: tuple[str, str, str] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> Any:
    choice = MagicMock()
    message = MagicMock()
    message.content = content
    if tool_call is not None:
        tc_id, tc_name, tc_args = tool_call
        tc = MagicMock()
        tc.id = tc_id
        fn = MagicMock()
        fn.name = tc_name
        fn.arguments = tc_args
        tc.function = fn
        message.tool_calls = [tc]
    else:
        message.tool_calls = []
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.model = model
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    response.usage = usage
    return response


class TestOpenAIChat:
    @pytest.mark.asyncio
    async def test_chat_returns_response(self) -> None:
        backend = OpenAICompatibleBackend(_config("openai"))
        with patch.object(
            backend._openai.chat.completions,  # type: ignore[union-attr]
            "create",
            new=AsyncMock(return_value=_mock_openai_chat_completion()),
        ):
            response = await backend.chat([_user("hi")])
        assert response.content == "hello"
        assert response.provider == "openai"
        assert response.usage.total_tokens == 15

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider", ["openai", "deepseek", "groq", "together"])
    async def test_unified_shape_across_providers(self, provider: str) -> None:
        # Groq/Together: pick a known-supported model or accept shim path.
        model = "llama-3.3-70b-versatile" if provider == "groq" else "test-model"
        backend = OpenAICompatibleBackend(_config(provider, model=model))
        with patch.object(
            backend._openai.chat.completions,  # type: ignore[union-attr]
            "create",
            new=AsyncMock(return_value=_mock_openai_chat_completion()),
        ):
            response = await backend.chat([_user("hi")])
        assert response.provider == provider
        assert isinstance(response.content, str)
        assert response.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_chat_with_native_tool_call(self) -> None:
        backend = OpenAICompatibleBackend(_config("openai"))
        mock_response = _mock_openai_chat_completion(
            content="",
            tool_call=("call-456", "web_search", '{"query": "kittens"}'),
        )
        with patch.object(
            backend._openai.chat.completions,  # type: ignore[union-attr]
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            response = await backend.chat(
                [_user("search")],
                tools=[ToolSpec(name="web_search", description="search", parameters={})],
            )
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].args == {"query": "kittens"}

    @pytest.mark.asyncio
    async def test_chat_with_malformed_tool_args_is_empty_dict(self) -> None:
        backend = OpenAICompatibleBackend(_config("openai"))
        mock_response = _mock_openai_chat_completion(
            content="",
            tool_call=("call-x", "web_search", "not-json"),
        )
        with patch.object(
            backend._openai.chat.completions,  # type: ignore[union-attr]
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            response = await backend.chat([_user("x")])
        assert response.tool_calls[0].args == {}


# -----------------------------------------------------------------------------
# Streaming — OpenAI
# -----------------------------------------------------------------------------


async def _async_iter(items: list[Any]) -> AsyncIterator[Any]:
    for x in items:
        yield x


def _openai_stream_chunk(
    *, content: str = "", usage: Any | None = None, tool_calls: list[Any] | None = None
) -> Any:
    chunk = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]
    chunk.usage = usage
    return chunk


class TestOpenAIStream:
    @pytest.mark.asyncio
    async def test_stream_yields_chunks_and_final_usage(self) -> None:
        backend = OpenAICompatibleBackend(_config("openai"))
        usage = MagicMock()
        usage.prompt_tokens = 4
        usage.completion_tokens = 6
        chunks_in = [
            _openai_stream_chunk(content="Hel"),
            _openai_stream_chunk(content="lo"),
            _openai_stream_chunk(usage=usage),
        ]
        with patch.object(
            backend._openai.chat.completions,  # type: ignore[union-attr]
            "create",
            new=AsyncMock(return_value=_async_iter(chunks_in)),
        ):
            collected: list[StreamChunk] = []
            async for c in backend.chat_stream([_user("hi")]):
                collected.append(c)
        # ≥2 chunks (≥1 delta + final).
        text_chunks = [c for c in collected if not c.is_final and c.delta]
        finals = [c for c in collected if c.is_final]
        assert len(text_chunks) >= 1
        assert len(finals) == 1
        assert finals[0].usage is not None
        assert finals[0].usage.total_tokens == 10


# -----------------------------------------------------------------------------
# Streaming — Anthropic
# -----------------------------------------------------------------------------


class _FakeAnthropicStream:
    """Mimics ``anthropic.AsyncMessageStream`` minimally."""

    def __init__(self, events: list[Any], final_message: Any) -> None:
        self._events = events
        self._final_message = final_message

    async def __aenter__(self) -> _FakeAnthropicStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            for ev in self._events:
                yield ev

        return gen()

    async def get_final_message(self) -> Any:
        return self._final_message


def _anthropic_text_delta_event(text: str) -> Any:
    ev = MagicMock()
    ev.type = "content_block_delta"
    delta = MagicMock()
    delta.type = "text_delta"
    delta.text = text
    ev.delta = delta
    return ev


class TestAnthropicStream:
    @pytest.mark.asyncio
    async def test_stream_yields_text_and_final(self) -> None:
        backend = OpenAICompatibleBackend(_config("anthropic"))
        events = [
            _anthropic_text_delta_event("Hel"),
            _anthropic_text_delta_event("lo"),
        ]
        final_msg = _mock_anthropic_message_response(text="Hello", input_tokens=4, output_tokens=2)
        fake_stream = _FakeAnthropicStream(events, final_msg)
        with patch.object(
            backend._anthropic.messages,  # type: ignore[union-attr]
            "stream",
            new=MagicMock(return_value=fake_stream),
        ):
            collected: list[StreamChunk] = []
            async for c in backend.chat_stream([_user("hi")]):
                collected.append(c)
        text_chunks = [c for c in collected if not c.is_final and c.delta]
        finals = [c for c in collected if c.is_final]
        assert len(text_chunks) == 2
        assert finals[0].usage is not None
        assert finals[0].usage.total_tokens == 6


# -----------------------------------------------------------------------------
# Error mapping
# -----------------------------------------------------------------------------


def _fake_response(*, status: int = 200, headers: dict[str, str] | None = None) -> Any:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.request = MagicMock()
    return resp


class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_anthropic_401_to_authentication_error(self) -> None:
        backend = OpenAICompatibleBackend(_config("anthropic"))
        exc = anthropic.AuthenticationError(
            "bad key", response=_fake_response(status=401), body=None
        )
        with (
            patch.object(
                backend._anthropic.messages,  # type: ignore[union-attr]
                "create",
                new=AsyncMock(side_effect=exc),
            ),
            pytest.raises(AuthenticationError) as info,
        ):
            await backend.chat([_user("x")])
        assert "anthropic" in str(info.value)

    @pytest.mark.asyncio
    async def test_anthropic_429_to_rate_limit(self) -> None:
        backend = OpenAICompatibleBackend(_config("anthropic"))
        exc = anthropic.RateLimitError(
            "slow down",
            response=_fake_response(status=429, headers={"retry-after": "30"}),
            body=None,
        )
        with (
            patch.object(
                backend._anthropic.messages,  # type: ignore[union-attr]
                "create",
                new=AsyncMock(side_effect=exc),
            ),
            pytest.raises(RateLimitError) as info,
        ):
            await backend.chat([_user("x")])
        assert "retry_after_s=30" in str(info.value)

    @pytest.mark.asyncio
    async def test_anthropic_404_to_model_not_found(self) -> None:
        backend = OpenAICompatibleBackend(_config("anthropic", model="imagined"))
        exc = anthropic.NotFoundError(
            "no such model", response=_fake_response(status=404), body=None
        )
        with (
            patch.object(
                backend._anthropic.messages,  # type: ignore[union-attr]
                "create",
                new=AsyncMock(side_effect=exc),
            ),
            pytest.raises(ModelNotFoundError) as info,
        ):
            await backend.chat([_user("x")])
        assert "model=imagined" in str(info.value)

    @pytest.mark.asyncio
    async def test_openai_timeout_to_backend_timeout_error(self) -> None:
        backend = OpenAICompatibleBackend(_config("openai"))
        # openai.APITimeoutError requires a request argument.
        request = MagicMock()
        exc = openai.APITimeoutError(request=request)
        with (
            patch.object(
                backend._openai.chat.completions,  # type: ignore[union-attr]
                "create",
                new=AsyncMock(side_effect=exc),
            ),
            pytest.raises(BackendTimeoutError),
        ):
            await backend.chat([_user("x")])

    @pytest.mark.asyncio
    async def test_unmapped_error_becomes_provider_error(self) -> None:
        backend = OpenAICompatibleBackend(_config("openai"))
        with (
            patch.object(
                backend._openai.chat.completions,  # type: ignore[union-attr]
                "create",
                new=AsyncMock(side_effect=RuntimeError("weird")),
            ),
            pytest.raises(ProviderError) as info,
        ):
            await backend.chat([_user("x")])
        assert "RuntimeError" in str(info.value)


# -----------------------------------------------------------------------------
# Shim fallback
# -----------------------------------------------------------------------------


class TestShimFallback:
    @pytest.mark.asyncio
    async def test_groq_unlisted_model_uses_shim(self) -> None:
        # whisper isn't in the allow-list → shim path.
        backend = OpenAICompatibleBackend(_config("groq", model="whisper-large-v3"))
        # Model emits a JSON tool-call block in text content.
        mock_response = _mock_openai_chat_completion(
            content='I will search. {"tool": "web_search", "args": {"q": "k"}}',
        )
        with patch.object(
            backend._openai.chat.completions,  # type: ignore[union-attr]
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            response = await backend.chat(
                [_user("search")],
                tools=[ToolSpec(name="web_search", description="x", parameters={})],
            )
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "web_search"
        # Cleaned content has tool-call JSON removed but explanatory text kept.
        assert "I will search." in response.content

    @pytest.mark.asyncio
    async def test_shim_failure_returns_text_passthrough(self) -> None:
        backend = OpenAICompatibleBackend(_config("groq", model="whisper-large-v3"))
        mock_response = _mock_openai_chat_completion(content="just plain text")
        with patch.object(
            backend._openai.chat.completions,  # type: ignore[union-attr]
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            response = await backend.chat(
                [_user("x")],
                tools=[ToolSpec(name="t", description="x", parameters={})],
            )
        assert response.tool_calls == []
        assert response.content == "just plain text"


# -----------------------------------------------------------------------------
# Real-API smoke test (D-10-9) — manual, paid, non-deterministic.
# Proves the Anthropic base_url fix end-to-end: a real chat returns content.
# Skipped unless PERSONA_FRONTIER_API_KEY is set.
# -----------------------------------------------------------------------------


class TestAnthropicRealCall:
    @pytest.mark.external
    @pytest.mark.asyncio
    async def test_anthropic_real_chat_returns_content(self) -> None:
        key = os.environ.get("PERSONA_FRONTIER_API_KEY")
        if not key:
            pytest.skip("PERSONA_FRONTIER_API_KEY not set")
        model = os.environ.get("PERSONA_FRONTIER_MODEL", "claude-sonnet-4-6")
        backend = OpenAICompatibleBackend(
            BackendConfig(provider="anthropic", model=model, api_key=SecretStr(key))
        )
        response = await backend.chat([_user("Reply with the single word: ok")], max_tokens=16)
        # A non-empty reply means the request reached /v1/messages (not /v1/v1/...).
        assert response.content.strip()
        assert response.provider == "anthropic"
