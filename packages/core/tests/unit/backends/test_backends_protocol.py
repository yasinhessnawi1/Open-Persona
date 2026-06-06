"""Tests for ``persona.backends.protocol`` — the ``ChatBackend`` Protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona.backends.protocol import ChatBackend
from persona.backends.types import ChatResponse, StreamChunk, TokenUsage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona.backends.types import ToolSpec
    from persona.schema.conversation import ConversationMessage


class _GoodBackend:
    """Minimal valid ChatBackend impl. Used to assert isinstance."""

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def supports_native_tools(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False

    async def chat(
        self,
        messages: list[ConversationMessage],  # noqa: ARG002
        *,
        tools: list[ToolSpec] | None = None,  # noqa: ARG002
        temperature: float = 0.0,  # noqa: ARG002
        max_tokens: int = 4096,  # noqa: ARG002
        stop: list[str] | None = None,  # noqa: ARG002
    ) -> ChatResponse:
        return ChatResponse(
            content="",
            usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            model=self.model_name,
            provider=self.provider_name,
            latency_ms=0.0,
        )

    async def chat_stream(
        self,
        messages: list[ConversationMessage],  # noqa: ARG002
        *,
        tools: list[ToolSpec] | None = None,  # noqa: ARG002
        temperature: float = 0.0,  # noqa: ARG002
        max_tokens: int = 4096,  # noqa: ARG002
        stop: list[str] | None = None,  # noqa: ARG002
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(
            delta="",
            is_final=True,
            usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )


class _MissingStreamBackend:
    """Lacks ``chat_stream``. Should fail isinstance check."""

    @property
    def provider_name(self) -> str:
        return "x"

    @property
    def model_name(self) -> str:
        return "y"

    @property
    def supports_native_tools(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False

    async def chat(
        self,
        messages: list[ConversationMessage],  # noqa: ARG002
        *,
        tools: list[ToolSpec] | None = None,  # noqa: ARG002
        temperature: float = 0.0,  # noqa: ARG002
        max_tokens: int = 4096,  # noqa: ARG002
        stop: list[str] | None = None,  # noqa: ARG002
    ) -> ChatResponse:
        raise NotImplementedError


class TestProtocolMembership:
    def test_good_backend_is_chat_backend(self) -> None:
        assert isinstance(_GoodBackend(), ChatBackend)

    def test_missing_stream_is_not_chat_backend(self) -> None:
        assert not isinstance(_MissingStreamBackend(), ChatBackend)

    def test_runtime_checkable(self) -> None:
        # The Protocol must be @runtime_checkable for the above to work.
        assert getattr(ChatBackend, "_is_runtime_protocol", False)


class TestStreamingShape:
    @pytest.mark.asyncio
    async def test_chat_stream_yields_async_iterator(self) -> None:
        backend = _GoodBackend()
        chunks: list[StreamChunk] = []
        async for chunk in backend.chat_stream(messages=[]):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0].is_final is True
        assert chunks[0].usage is not None

    @pytest.mark.asyncio
    async def test_chat_returns_chat_response(self) -> None:
        backend = _GoodBackend()
        response = await backend.chat(messages=[])
        assert isinstance(response, ChatResponse)
        assert response.provider == "test"


class TestProperties:
    def test_required_properties(self) -> None:
        backend = _GoodBackend()
        assert backend.provider_name == "test"
        assert backend.model_name == "test-model"
        assert backend.supports_native_tools is False
