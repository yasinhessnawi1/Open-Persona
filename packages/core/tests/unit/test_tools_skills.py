"""Tests for ``persona.schema.tools`` and ``persona.schema.skills`` (T06)."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime in test fixtures
from typing import Any

import pytest
from persona.schema.skills import SkillSpec
from persona.schema.tools import Tool, ToolCall, ToolResult
from pydantic import ValidationError


class _MockTool:
    """Duck-typed Tool implementation for protocol-conformance testing."""

    name = "mock"
    description = "a stub tool used in tests"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def __call__(self, **kwargs: Any) -> ToolResult:  # noqa: ANN401
        return ToolResult(tool_name=self.name, content=str(kwargs))


class TestToolProtocol:
    def test_runtime_checkable_recognises_duck_typed_implementation(self) -> None:
        assert isinstance(_MockTool(), Tool)

    def test_object_missing_attribute_is_not_a_tool(self) -> None:
        class Incomplete:
            name = "x"
            description = "y"
            # missing parameters_schema and __call__

        assert not isinstance(Incomplete(), Tool)


class TestToolCall:
    def test_minimal_construction(self) -> None:
        call = ToolCall(name="web_search")
        assert call.args == {}
        assert call.call_id == ""

    def test_with_args(self) -> None:
        call = ToolCall(name="web_search", args={"query": "x", "max_results": 5})
        assert call.args["query"] == "x"

    def test_frozen(self) -> None:
        call = ToolCall(name="t")
        with pytest.raises(ValidationError):
            call.name = "u"  # type: ignore[misc]

    def test_extra_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            ToolCall(name="t", unknown="x")  # type: ignore[call-arg]


class TestToolResult:
    def test_success_result(self) -> None:
        res = ToolResult(tool_name="web_search", content="ok")
        assert not res.is_error
        assert res.metadata == {}

    def test_error_result(self) -> None:
        res = ToolResult(
            tool_name="web_search",
            content="rate limited",
            is_error=True,
            metadata={"retry_after_s": "30"},
        )
        assert res.is_error
        assert res.metadata["retry_after_s"] == "30"

    def test_frozen(self) -> None:
        res = ToolResult(tool_name="t", content="c")
        with pytest.raises(ValidationError):
            res.content = "d"  # type: ignore[misc]


class TestSkillSpec:
    def test_minimal_construction(self, tmp_path: Path) -> None:
        spec = SkillSpec(name="legal_research", description="researches law", path=tmp_path)
        assert spec.when_to_use is None
        assert spec.path == tmp_path

    def test_empty_name_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            SkillSpec(name="", description="d", path=tmp_path)

    def test_empty_description_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            SkillSpec(name="n", description="", path=tmp_path)

    def test_frozen(self, tmp_path: Path) -> None:
        spec = SkillSpec(name="n", description="d", path=tmp_path)
        with pytest.raises(ValidationError):
            spec.name = "x"  # type: ignore[misc]

    def test_extra_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            SkillSpec(
                name="n",
                description="d",
                path=tmp_path,
                unknown="x",  # type: ignore[call-arg]
            )
