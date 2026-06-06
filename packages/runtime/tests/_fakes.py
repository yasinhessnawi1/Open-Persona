"""Test fakes for the runtime loop (T07).

- ``ScriptedBackend``: a ChatBackend whose ``chat_stream`` replays a scripted
  list of "rounds", each round either text or a single tool call. Emits one
  complete ``ToolCallDelta`` per tool call (shim shape) so the loop's
  delta-accumulation reconstruction (D-05-13) is exercised without a live
  provider.
- ``FakeStore``: a minimal in-memory MemoryStore recording writes (so the
  episodic write-back timing test can assert zero/one writes).
"""

# ruff: noqa: ANN401, ARG002, D102 — test doubles with intentionally loose sigs.

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from persona.backends.types import StreamChunk, TokenUsage, ToolCallDelta

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona.backends.types import ToolSpec
    from persona.schema.chunks import PersonaChunk, WriteSource
    from persona.schema.conversation import ConversationMessage


class ScriptedRound:
    """One scripted backend round: either text, or one tool call."""

    def __init__(
        self,
        *,
        text: str = "",
        text_deltas: list[str] | None = None,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        call_id: str = "call-1",
    ) -> None:
        self.text = text
        # When set, the round streams these as SEPARATE chunks (exercises
        # delta-by-delta streaming); otherwise `text` is emitted as one chunk.
        self.text_deltas = text_deltas
        self.tool_name = tool_name
        self.tool_args = tool_args or {}
        self.call_id = call_id


class ScriptedBackend:
    """A ChatBackend that replays scripted rounds on successive chat_stream calls.

    Each call to ``chat_stream`` consumes the next round. A text round yields
    the text then a final chunk. A tool round yields one complete
    ``ToolCallDelta`` (shim shape) then a final chunk with no text.
    """

    def __init__(
        self,
        rounds: list[ScriptedRound],
        *,
        provider_name: str = "anthropic",  # a provider the formatter knows (D-03-6)
        model_name: str = "claude-sonnet-4-6",
        chat_script: list[Any] | None = None,
        supports_vision: bool = False,
    ) -> None:
        self._rounds = rounds
        self._index = 0
        self._provider_name = provider_name
        self._model_name = model_name
        self._supports_vision = supports_vision
        self.chat_stream_calls = 0
        # The agentic loop (spec 06) drives non-streaming chat() through a
        # scripted SEQUENCE of ChatResponses (plan -> tool -> tool -> final).
        # When chat_script is provided, chat() consumes it in order; when it is
        # None, chat() keeps the fixed-"SUMMARY" behaviour the conversation
        # loop's summariser relies on (backward-compatible — research §7).
        self._chat_script: list[Any] = list(chat_script) if chat_script else []
        self._chat_index = 0
        self.chat_calls = 0

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
        return self._supports_vision

    async def chat(
        self,
        messages: list[ConversationMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> Any:
        from persona.backends.types import ChatResponse

        self.chat_calls += 1
        # Agentic-loop path: replay the scripted ChatResponse sequence.
        if self._chat_script:
            if self._chat_index < len(self._chat_script):
                response = self._chat_script[self._chat_index]
                self._chat_index += 1
                return response
            # Exhausted: a defensive empty final (the loop should have stopped).
            return ChatResponse(
                content="",
                tool_calls=[],
                usage=TokenUsage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
                model=self._model_name,
                provider=self._provider_name,
                latency_ms=0.0,
            )
        # Conversation-loop summariser path: a fixed "SUMMARY" response.
        return ChatResponse(
            content="SUMMARY",
            tool_calls=[],
            usage=TokenUsage(prompt_tokens=5, completion_tokens=2, total_tokens=7),
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
        self.chat_stream_calls += 1
        if self._index >= len(self._rounds):
            # No more scripted rounds: yield empty text + final (defensive).
            yield StreamChunk(
                delta="",
                is_final=True,
                usage=TokenUsage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
            )
            return
        rnd = self._rounds[self._index]
        self._index += 1
        if rnd.tool_name is not None:
            yield StreamChunk(
                delta="",
                tool_call_delta=ToolCallDelta(
                    call_id=rnd.call_id,
                    name_delta=rnd.tool_name,
                    arguments_delta=json.dumps(rnd.tool_args),
                ),
            )
        elif rnd.text_deltas:
            for piece in rnd.text_deltas:
                yield StreamChunk(delta=piece)
        elif rnd.text:
            yield StreamChunk(delta=rnd.text)
        yield StreamChunk(
            delta="",
            is_final=True,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FakeStore:
    """Minimal in-memory MemoryStore double recording writes and serving queries."""

    def __init__(self, *, query_results: list[PersonaChunk] | None = None) -> None:
        self._query_results = query_results or []
        self.writes: list[list[PersonaChunk]] = []
        self._all: list[PersonaChunk] = []

    def write(
        self,
        persona_id: str,
        chunks: list[PersonaChunk],
        *,
        source: WriteSource,
        written_by: str | None = None,
        reason: str | None = None,
        force: bool = False,
    ) -> None:
        self.writes.append(list(chunks))
        self._all.extend(chunks)

    def query(self, persona_id: str, query: str, top_k: int, **filters: Any) -> list[PersonaChunk]:
        return list(self._query_results[:top_k])

    def get_all(self, persona_id: str, *, include_superseded: bool = False) -> list[PersonaChunk]:
        return list(self._all)

    def delete(self, persona_id: str) -> None: ...

    def remove_documents(self, persona_id: str, doc_ids: list[str]) -> None: ...

    def history(self, persona_id: str, logical_id: str) -> list[PersonaChunk]:
        return []

    def rollback(
        self,
        persona_id: str,
        logical_id: str,
        to_version: int,
        *,
        source: WriteSource,
        written_by: str | None = None,
        reason: str | None = None,
    ) -> None: ...
