"""Tests for the text_summarize built-in tool (spec 26 T07).

``text_summarize`` is runtime-wired: it takes a ChatBackend. A fake backend is
injected here so no model is called. (The runtime-factory wiring proof for the
no-§2.9-gap requirement lives in the persona-api test suite —
D-26-X-text-summarize-wiring-test-kind.)
"""

# ruff: noqa: ANN401, ARG002
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from persona.backends import ProviderError
from persona.tools.builtin.text_summarize import _MAX_INPUT_CHARS, make_text_summarize_tool
from persona.tools.protocol import AsyncTool

if TYPE_CHECKING:
    from persona.schema.conversation import ConversationMessage


@dataclass
class _FakeResponse:
    content: str


class _FakeBackend:
    """Captures the messages it was called with and returns a fixed summary."""

    def __init__(self, reply: str = "A short summary.") -> None:
        self.reply = reply
        self.calls: list[list[ConversationMessage]] = []

    async def chat(self, messages: list[ConversationMessage], **kwargs: Any) -> _FakeResponse:
        self.calls.append(messages)
        return _FakeResponse(content=self.reply)


class _FailingBackend:
    async def chat(self, messages: list[ConversationMessage], **kwargs: Any) -> _FakeResponse:
        raise ProviderError("provider down", context={"provider": "test"})


class _EmptyBackend:
    async def chat(self, messages: list[ConversationMessage], **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(content="   ")


class TestTextSummarizeTool:
    def test_is_async_tool(self) -> None:
        tool_inst = make_text_summarize_tool(_FakeBackend())  # type: ignore[arg-type]
        assert isinstance(tool_inst, AsyncTool)
        assert tool_inst.name == "text_summarize"

    @pytest.mark.asyncio
    async def test_happy_path_returns_summary(self) -> None:
        backend = _FakeBackend(reply="The cat sat on the mat.")
        tool_inst = make_text_summarize_tool(backend)  # type: ignore[arg-type]
        result = await tool_inst.execute(text="A long passage about a cat.", max_words=20)
        assert result.is_error is False
        assert result.content == "The cat sat on the mat."
        assert result.data is not None
        assert result.data["summary"] == "The cat sat on the mat."
        # The system prompt carries the word budget.
        assert "20 words" in backend.calls[0][0].content

    @pytest.mark.asyncio
    async def test_empty_input_errors_without_calling_model(self) -> None:
        backend = _FakeBackend()
        tool_inst = make_text_summarize_tool(backend)  # type: ignore[arg-type]
        result = await tool_inst.execute(text="   ")
        assert result.is_error is True
        assert backend.calls == []

    @pytest.mark.asyncio
    async def test_oversized_input_truncated_flag(self) -> None:
        backend = _FakeBackend()
        tool_inst = make_text_summarize_tool(backend)  # type: ignore[arg-type]
        result = await tool_inst.execute(text="x" * (_MAX_INPUT_CHARS + 100))
        assert result.is_error is False
        assert result.data is not None
        assert result.data["input_truncated"] is True
        # The body passed to the model was capped.
        assert len(backend.calls[0][1].content) == _MAX_INPUT_CHARS

    @pytest.mark.asyncio
    async def test_backend_error_returns_error_not_raises(self) -> None:
        tool_inst = make_text_summarize_tool(_FailingBackend())  # type: ignore[arg-type]
        result = await tool_inst.execute(text="summarize me")
        assert result.is_error is True
        assert "Summarization failed" in result.content

    @pytest.mark.asyncio
    async def test_empty_model_reply_errors(self) -> None:
        tool_inst = make_text_summarize_tool(_EmptyBackend())  # type: ignore[arg-type]
        result = await tool_inst.execute(text="summarize me")
        assert result.is_error is True
        assert "empty summary" in result.content
