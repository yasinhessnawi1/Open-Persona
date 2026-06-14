"""``text_summarize`` built-in tool — small-tier LLM summarization (spec 26 T07).

Unlike the other spec-26 built-ins, ``text_summarize`` is **runtime-wired**: it
needs a model, so it is NOT registered by ``build_default_toolbox``. The
composition root (``persona_api.services.runtime_factory``) builds it with the
**small**-tier :class:`~persona.backends.ChatBackend` and folds it in as an
``extra_tool`` — exactly like ``code_execution`` / ``generate_image`` (T1 /
D-26-7). Its no-§2.9-wiring-gap test is therefore the runtime-factory kind
(D-26-X-text-summarize-wiring-test-kind).

Abstractive LLM summarization (small tier) is on-voice and adds zero
dependency, unlike heavy extractive stacks (sumy/nltk/numpy). Oversized inputs
are truncated before the call to bound cost. Backend failures
(:class:`~persona.backends.ProviderError`) are returned as
``ToolResult(is_error=True)`` — never raised (D-03-5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.backends import ProviderError
from persona.logging import get_logger
from persona.schema.conversation import ConversationMessage
from persona.schema.tools import ToolResult
from persona.tools.protocol import AsyncTool, tool

if TYPE_CHECKING:
    from persona.backends import ChatBackend

__all__ = ["make_text_summarize_tool"]

_logger = get_logger("tools.text_summarize")

# Truncate input before the call so a giant blob can't run up cost. ~48k chars
# is well within a small-tier context once the prompt scaffolding is added.
_MAX_INPUT_CHARS = 48_000


def make_text_summarize_tool(backend: ChatBackend) -> AsyncTool:
    """Build the ``text_summarize`` :class:`AsyncTool`.

    Args:
        backend: The **small**-tier chat backend the tool calls to produce the
            summary (injected by the runtime composition root — the tool is not
            registered by ``build_default_toolbox``).

    Returns:
        An :class:`AsyncTool` named ``text_summarize``. Backend failures are
        returned as ``ToolResult(is_error=True)`` — never raised.
    """

    @tool(
        name="text_summarize",
        description=(
            "YOU CAN summarize long text. Use this tool to condense a long "
            "passage into a short summary. Provide 'text' and optionally "
            "'max_words' (default 100). The summary preserves the key points."
        ),
    )
    async def text_summarize(text: str, max_words: int = 100) -> ToolResult:
        stripped = text.strip()
        if not stripped:
            return ToolResult(
                tool_name="text_summarize",
                content="Nothing to summarize (empty input).",
                is_error=True,
            )
        truncated_input = len(stripped) > _MAX_INPUT_CHARS
        body = stripped[:_MAX_INPUT_CHARS]
        target_words = max(10, min(max_words, 500))

        now = datetime.now(UTC)
        messages = [
            ConversationMessage(
                role="system",
                content=(
                    f"Summarize the user's text in at most {target_words} words. "
                    "Preserve the key facts and the original tone. Output ONLY the "
                    "summary — no preamble, no commentary."
                ),
                created_at=now,
            ),
            ConversationMessage(role="user", content=body, created_at=now),
        ]
        try:
            response = await backend.chat(
                messages,
                temperature=0.0,
                max_tokens=min(1024, target_words * 6),
            )
        except ProviderError as e:
            _logger.warning("text_summarize backend error", error=type(e).__name__)
            return ToolResult(
                tool_name="text_summarize",
                content=f"Summarization failed: {type(e).__name__}: {e}",
                is_error=True,
            )

        summary = response.content.strip()
        if not summary:
            return ToolResult(
                tool_name="text_summarize",
                content="The model returned an empty summary.",
                is_error=True,
            )
        return ToolResult(
            tool_name="text_summarize",
            content=summary,
            data={
                "summary": summary,
                "input_truncated": truncated_input,
                "max_words": target_words,
            },
        )

    return text_summarize
