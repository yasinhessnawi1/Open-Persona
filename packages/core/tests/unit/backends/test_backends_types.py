"""Tests for ``persona.backends.types`` — response, stream, and tool shapes."""

from __future__ import annotations

from typing import Any

import pytest
from persona.backends.types import (
    ChatResponse,
    StreamChunk,
    TokenUsage,
    ToolCallDelta,
    ToolSpec,
    tool_spec_from_tool,
)
from persona.schema.tools import ToolCall, ToolResult
from pydantic import ValidationError


class TestTokenUsage:
    def test_construct_and_serialise_roundtrip(self) -> None:
        u = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        round_tripped = TokenUsage.model_validate(u.model_dump())
        assert round_tripped == u

    def test_total_must_equal_sum(self) -> None:
        with pytest.raises(ValidationError, match="does not equal"):
            TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=99)

    def test_zero_counts_allowed(self) -> None:
        u = TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        assert u.total_tokens == 0

    def test_negative_counts_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TokenUsage(prompt_tokens=-1, completion_tokens=0, total_tokens=-1)

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TokenUsage.model_validate(
                {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                    "extra": "nope",
                }
            )

    def test_frozen(self) -> None:
        u = TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        with pytest.raises(ValidationError):
            u.prompt_tokens = 99  # type: ignore[misc]


class TestToolSpec:
    def test_construct(self) -> None:
        spec = ToolSpec(
            name="web_search",
            description="Search the web.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
        assert spec.name == "web_search"
        assert spec.parameters["type"] == "object"

    def test_parameters_kept_as_dict(self) -> None:
        # JSON Schema correctness is the provider's problem; we just carry the dict.
        spec = ToolSpec(name="x", description="y", parameters={"anything": True})
        assert spec.parameters == {"anything": True}

    def test_frozen_and_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            ToolSpec.model_validate(
                {
                    "name": "x",
                    "description": "y",
                    "parameters": {},
                    "version": "v1",
                }
            )


class TestToolCallDelta:
    def test_minimum_construction(self) -> None:
        d = ToolCallDelta(call_id="abc")
        assert d.call_id == "abc"
        assert d.name_delta == ""
        assert d.arguments_delta == ""

    def test_with_args_delta(self) -> None:
        d = ToolCallDelta(call_id="abc", name_delta="web_", arguments_delta='{"qu')
        assert d.arguments_delta == '{"qu'


class TestChatResponse:
    def _usage(self) -> TokenUsage:
        return TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

    def test_construct_minimal(self) -> None:
        resp = ChatResponse(
            content="hi",
            usage=self._usage(),
            model="claude-sonnet-4-6",
            provider="anthropic",
            latency_ms=12.5,
        )
        assert resp.content == "hi"
        assert resp.tool_calls == []
        assert resp.latency_ms == 12.5

    def test_construct_with_tool_calls(self) -> None:
        tc = ToolCall(name="web_search", args={"query": "x"}, call_id="t1")
        resp = ChatResponse(
            content="",
            tool_calls=[tc],
            usage=self._usage(),
            model="gpt-4o",
            provider="openai",
            latency_ms=80.0,
        )
        assert resp.tool_calls == [tc]
        assert resp.content == ""

    def test_serialise_roundtrip(self) -> None:
        resp = ChatResponse(
            content="hello",
            usage=self._usage(),
            model="x",
            provider="y",
            latency_ms=0.0,
        )
        dumped = resp.model_dump()
        round_tripped = ChatResponse.model_validate(dumped)
        assert round_tripped == resp

    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChatResponse(
                content="",
                usage=self._usage(),
                model="x",
                provider="y",
                latency_ms=-1.0,
            )

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChatResponse.model_validate(
                {
                    "content": "",
                    "usage": self._usage().model_dump(),
                    "model": "x",
                    "provider": "y",
                    "latency_ms": 0.0,
                    "stop_reason": "end_turn",
                }
            )


class TestStreamChunk:
    def test_simple_delta(self) -> None:
        c = StreamChunk(delta="hello")
        assert c.delta == "hello"
        assert c.is_final is False
        assert c.usage is None
        assert c.tool_call_delta is None

    def test_final_with_usage(self) -> None:
        u = TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        c = StreamChunk(delta="", is_final=True, usage=u)
        assert c.is_final is True
        assert c.usage == u

    def test_with_tool_call_delta(self) -> None:
        tcd = ToolCallDelta(call_id="abc", arguments_delta='{"x')
        c = StreamChunk(delta="", tool_call_delta=tcd)
        assert c.tool_call_delta == tcd

    def test_empty_delta_allowed(self) -> None:
        c = StreamChunk(delta="")
        assert c.delta == ""

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StreamChunk.model_validate({"delta": "x", "weird": True})


class TestToolSpecFromTool:
    def test_converts_tool_to_spec(self) -> None:
        class FakeTool:
            name: str = "web_search"
            description: str = "Search."
            parameters_schema: dict[str, Any] = {"type": "object"}

            def __call__(self, **kwargs: Any) -> ToolResult:  # noqa: ANN401, ARG002 — Tool Protocol shape
                return ToolResult(tool_name=self.name, content="ok")

        spec = tool_spec_from_tool(FakeTool())
        assert spec.name == "web_search"
        assert spec.description == "Search."
        assert spec.parameters == {"type": "object"}

    def test_copies_parameters_dict(self) -> None:
        # Mutating the source dict after conversion must not affect the spec.
        class FakeTool:
            name = "x"
            description = "y"
            parameters_schema: dict[str, Any] = {"a": 1}

            def __call__(self, **kwargs: Any) -> ToolResult:  # noqa: ANN401, ARG002 — Tool Protocol shape
                return ToolResult(tool_name=self.name, content="")

        source = FakeTool()
        spec = tool_spec_from_tool(source)
        source.parameters_schema["a"] = 999
        assert spec.parameters == {"a": 1}
