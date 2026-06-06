"""Test-only ``ChatBackend`` implementation.

Lives in ``tests/`` (not ``src/``) per D-02-12 — production code does not
ship an ``echo`` provider. Tests inject this backend explicitly.
"""

# ruff: noqa: ANN401, ARG002

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.backends.types import ChatResponse, StreamChunk, TokenUsage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona.backends.types import ToolSpec
    from persona.schema.conversation import ConversationMessage


class MockChatBackend:
    """Deterministic ``ChatBackend`` for CLI / runtime integration tests.

    Echoes the last user message back as the assistant reply, populates
    realistic token counts, and supports the streaming method (yields one
    text chunk + one final chunk with usage).

    Replaces the spec-01 ``EchoBackend`` stub that used to live in
    ``cli/_echo.py``. Constructed and injected by individual tests.
    """

    def __init__(
        self,
        *,
        model_name: str = "mock-model",
        provider_name: str = "mock",
    ) -> None:
        self._model_name = model_name
        self._provider_name = provider_name

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def supports_native_tools(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False

    async def chat(
        self,
        messages: list[ConversationMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> ChatResponse:
        content = _echo_text(messages)
        usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        return ChatResponse(
            content=content,
            tool_calls=[],
            usage=usage,
            model=self._model_name,
            provider=self._provider_name,
            latency_ms=0.0,
        )

    async def chat_stream(
        self,
        messages: list[ConversationMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        content = _echo_text(messages)
        # Yield in two halves to exercise the streaming path.
        midpoint = max(1, len(content) // 2)
        yield StreamChunk(delta=content[:midpoint])
        yield StreamChunk(delta=content[midpoint:])
        yield StreamChunk(
            delta="",
            is_final=True,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


def _echo_text(messages: list[ConversationMessage]) -> str:
    """Find the last user message and echo it."""
    last_user = next(
        (m for m in reversed(messages) if m.role == "user"),
        None,
    )
    if last_user is None:
        return "I would say: (no user message yet)"
    return f"I would say: {last_user.content}"
