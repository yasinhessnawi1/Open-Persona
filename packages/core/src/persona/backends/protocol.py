"""The :class:`ChatBackend` Protocol.

One Protocol; every model backend (Anthropic, OpenAI, DeepSeek, Groq,
Together, Ollama, local HuggingFace) implements it. Callers depend on
this Protocol — not on any provider SDK or transport.

See ``docs/specs/spec_02/decisions.md`` D-02-5 for the
``chat_stream`` signature rationale (``def -> AsyncIterator[StreamChunk]``,
not ``async def``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona.backends.types import ChatResponse, StreamChunk, ToolSpec
    from persona.schema.conversation import ConversationMessage

__all__ = ["ChatBackend"]


@runtime_checkable
class ChatBackend(Protocol):
    """Async chat backend — the only surface the runtime talks to.

    Implementations are constructed from a :class:`BackendConfig` and
    expose two operations:

    * :meth:`chat` — single round-trip; returns a complete
      :class:`ChatResponse` with usage and tool calls.
    * :meth:`chat_stream` — streaming generator; yields
      :class:`StreamChunk` objects ending with ``is_final=True``.

    Construction-time failures (missing API key, unknown model when
    detectable cheaply) raise :class:`AuthenticationError` / domain
    exceptions immediately — never wait until first call. ``HFLocalBackend``
    is the exception: it lazy-loads weights, but config validation still
    happens at ``__init__`` (D-02-10).
    """

    @property
    def provider_name(self) -> str:
        """Identifier of the configured provider (``"anthropic"``, ...).

        Lowercase, ASCII. Stable across releases. Used for logging,
        observability, and the ``provider`` field of :class:`ChatResponse`.
        """

    @property
    def model_name(self) -> str:
        """Model identifier as the provider knows it.

        Echoed back in :class:`ChatResponse.model`. Backends do not
        normalise — what the caller configured is what the response
        reports.
        """

    @property
    def supports_native_tools(self) -> bool:
        """True iff this backend uses the provider's native tool calling.

        False means the backend uses the prompt-based shim (D-02-6).
        Callers SHOULD NOT branch on this — the shim and native paths
        both populate :class:`ChatResponse.tool_calls` identically. The
        property exists for observability and capability dashboards.
        """

    async def chat(
        self,
        messages: list[ConversationMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> ChatResponse:
        """Single-shot chat. Returns when the model is done.

        Args:
            messages: Conversation prefix in chronological order. Each
                :class:`ConversationMessage` carries role, content, tz-aware
                ``created_at``, and optional metadata.
            tools: Tools the model may call. Empty / None means text-only.
            temperature: Sampling temperature; 0.0 = deterministic.
            max_tokens: Cap on the response. Provider-side max applies.
            stop: Optional stop sequences. None / empty means no override.

        Returns:
            :class:`ChatResponse` with content, parsed ``tool_calls``,
            token usage, and client-measured latency.

        Raises:
            AuthenticationError: missing or rejected credentials.
            RateLimitError: provider returned 429.
            ModelNotFoundError: model name unknown to provider.
            BackendTimeoutError: HTTP timeout.
            ProviderError: anything else.
        """

    def chat_stream(
        self,
        messages: list[ConversationMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Streaming chat. Yields chunks until ``is_final=True``.

        Concrete implementations are ``async def`` + ``yield``; the
        Protocol declares plain ``def`` returning :class:`AsyncIterator`
        because that is what an async generator's type is from the
        consumer's perspective (PEP 525). Consumers iterate with
        ``async for``, no extra ``await``.

        Args: see :meth:`chat`.

        Yields:
            :class:`StreamChunk` objects. Intermediate chunks have
            ``is_final=False`` and may carry text fragments
            (``delta``) and/or tool-call fragments
            (``tool_call_delta``). The final chunk has ``is_final=True``
            and populated ``usage``.

        Raises: see :meth:`chat`. Errors from the provider surface during
            iteration, not at call time.
        """
