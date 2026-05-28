"""Unit tests for persona_runtime.agentic.step (T02, D-06-1, D-06-3)."""

from __future__ import annotations

import pytest
from persona.schema.tools import ToolCall, ToolResult
from persona_runtime.agentic.step import Step, StepType
from pydantic import ValidationError


class TestStepType:
    def test_values(self) -> None:
        assert StepType.TOOL_CALL == "tool_call"
        assert StepType.ASK_USER == "ask_user"
        assert StepType.FINAL == "final"
        assert StepType.REASONING == "reasoning"
        assert StepType.ERROR == "error"


class TestStepConstruction:
    def test_minimal_step(self) -> None:
        step = Step(type=StepType.REASONING, content="thinking out loud")
        assert step.type is StepType.REASONING
        assert step.content == "thinking out loud"
        assert step.tool_calls == []
        assert step.results == []
        assert step.tokens == 0
        assert step.latency_ms == 0.0

    def test_tool_call_step_with_results(self) -> None:
        call = ToolCall(name="web_search", args={"query": "mould law"}, call_id="c-1")
        result = ToolResult(tool_name="web_search", content="found 3 results", call_id="c-1")
        step = Step(type=StepType.TOOL_CALL, tool_calls=[call], results=[result])
        assert step.tool_calls[0].name == "web_search"
        assert step.results[0].content == "found 3 results"
        assert step.results[0].is_error is False

    def test_results_accept_is_error_shape(self) -> None:
        # D-03-3: failure is is_error=True + content, NOT an error field.
        result = ToolResult(tool_name="bogus", content="not available", is_error=True)
        step = Step(type=StepType.TOOL_CALL, results=[result])
        assert step.results[0].is_error is True
        assert step.results[0].content == "not available"

    def test_ask_user_step(self) -> None:
        step = Step(type=StepType.ASK_USER, question="Which apartment?", user_answer="3B")
        assert step.question == "Which apartment?"
        assert step.user_answer == "3B"

    def test_telemetry_fields(self) -> None:
        step = Step(
            type=StepType.FINAL, content="done", tier_used="frontier", tokens=420, latency_ms=12.5
        )
        assert step.tier_used == "frontier"
        assert step.tokens == 420
        assert step.latency_ms == 12.5


class TestStepInvariants:
    def test_frozen(self) -> None:
        step = Step(type=StepType.FINAL, content="x")
        with pytest.raises(ValidationError):
            step.content = "y"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Step(type=StepType.FINAL, bogus="nope")  # type: ignore[call-arg]

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Step(type=StepType.FINAL, tokens=-1)

    def test_json_round_trip(self) -> None:
        call = ToolCall(name="echo", args={"text": "hi"}, call_id="c-9")
        result = ToolResult(tool_name="echo", content="hi", call_id="c-9")
        step = Step(
            type=StepType.TOOL_CALL, tool_calls=[call], results=[result], tier_used="mid", tokens=7
        )
        restored = Step.model_validate_json(step.model_dump_json())
        assert restored == step
