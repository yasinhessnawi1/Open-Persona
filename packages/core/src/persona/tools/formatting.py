"""Provider-aware tool-result formatter for spec 03.

Converts a (:class:`ToolCall`, :class:`ToolResult`) pair into a
:class:`ConversationMessage` whose role and content shape match the
provider's expected tool-result message. The runtime (spec 05) calls this
once per tool dispatch and the resulting message is appended to the
conversation history before the next model call.

Provider shapes (research.md §7):

- **Anthropic**: a ``tool_result`` content block inside a ``user`` message,
  carrying ``tool_use_id``, ``content``, optional ``is_error: true``. We emit
  ``role="tool"`` with the raw result text and let ``_message_to_anthropic``
  build the structured block from ``metadata`` — same shape as the OpenAI
  branch (spec 11 launch fix).
- **OpenAI / DeepSeek / Groq / Together / NVIDIA** (Spec 20): a separate
  message with ``role="tool"``, ``tool_call_id``, ``name``, ``content``. The
  error flag is conveyed by prefixing ``content`` with ``"Error: "``. NVIDIA
  uses the same OpenAI-compat tool-result shape as the rest of the openai-SDK
  providers per D-20-X-nvidia-allow-set-extend (the atomic invariant is a
  SIX-touch including this formatter).
- **Ollama / local (HF) shim**: plain-text message with ``role="user"``
  formatted as ``"<tool_name> returned: <content>"``. The shim's bookkeeping
  picks tool-call ids from ``metadata``.

Unknown ``provider_name`` raises :class:`ValueError` — a programmer-error
boundary, NOT a domain exception (D-03-6).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.schema.conversation import ConversationMessage

if TYPE_CHECKING:
    from persona.schema.tools import ToolCall, ToolResult

__all__ = ["format_tool_result"]


def format_tool_result(
    tool_call: ToolCall,
    result: ToolResult,
    *,
    provider_name: str,
) -> ConversationMessage:
    """Format a tool result for the given provider's API.

    Args:
        tool_call: The originating :class:`ToolCall` (carries ``call_id``).
        result: The :class:`ToolResult` returned by the tool dispatch.
        provider_name: One of ``anthropic``, ``openai``, ``deepseek``,
            ``groq``, ``together``, ``nvidia`` (Spec 20), ``ollama``, ``local``.

    Returns:
        A :class:`ConversationMessage` with the correct ``role`` and
        ``content`` for the provider. ``metadata`` carries the tool-call
        bookkeeping every provider needs (``tool_call_id``, ``tool_name``,
        ``is_error``, ``provider_format``).

    Raises:
        ValueError: If ``provider_name`` is not one of the eight supported
            providers. This is a programmer-error boundary (D-03-6), not a
            domain exception.
    """
    now = datetime.now(UTC)

    match provider_name:
        case "anthropic":
            # Spec 11 launch fix: emit role="tool" with the raw result text +
            # metadata, mirroring the OpenAI/DeepSeek path. `_message_to_anthropic`
            # then lifts it into a proper structured `tool_result` block list on
            # a user message. Previously we JSON-encoded the block into `content`
            # (a string) which Anthropic doesn't recognise — broke every native
            # tool round-trip the moment Astrid/Kai actually used a tool.
            return ConversationMessage(
                role="tool",
                content=result.content,
                created_at=now,
                metadata={
                    "tool_call_id": tool_call.call_id,
                    "tool_name": result.tool_name,
                    "is_error": str(result.is_error),
                    "provider_format": "anthropic",
                },
            )

        case "openai" | "deepseek" | "groq" | "together" | "nvidia" | "cloudflare":
            content = result.content
            if result.is_error and not content.startswith("Error:"):
                content = f"Error: {content}"
            return ConversationMessage(
                role="tool",
                content=content,
                created_at=now,
                metadata={
                    "tool_call_id": tool_call.call_id,
                    "tool_name": result.tool_name,
                    "is_error": str(result.is_error),
                    "provider_format": "openai",
                },
            )

        case "ollama" | "local":
            return ConversationMessage(
                role="user",
                content=f"{result.tool_name} returned: {result.content}",
                created_at=now,
                metadata={
                    "tool_call_id": tool_call.call_id,
                    "tool_name": result.tool_name,
                    "is_error": str(result.is_error),
                    "provider_format": "shim",
                },
            )

        case _:
            msg = f"Unknown provider_name: {provider_name!r}"
            raise ValueError(msg)
