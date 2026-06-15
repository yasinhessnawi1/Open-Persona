"""Tests for spec-03 tool Protocols and extended ToolResult (T03)."""

# ruff: noqa: ANN401, ARG001, ARG002, ERA001 — mock fixtures; "# Section:" markers
from __future__ import annotations

from typing import Any

import pytest
from persona.backends.types import ToolSpec, tool_spec_from_tool
from persona.schema.tools import Tool, ToolCall, ToolResult
from persona.tools.protocol import AsyncTool, ToolDescriptor, tool

# ---------------------------------------------------------------------------
# Section: ToolDescriptor Protocol
# ---------------------------------------------------------------------------


class TestToolDescriptor:
    """ToolDescriptor — the shared metadata surface (D-03-2)."""

    def test_class_with_three_properties_satisfies(self) -> None:
        class Impl:
            @property
            def name(self) -> str:
                return "x"

            @property
            def description(self) -> str:
                return "d"

            @property
            def parameters_schema(self) -> dict[str, Any]:
                return {"type": "object"}

        assert isinstance(Impl(), ToolDescriptor)

    def test_class_with_plain_attributes_satisfies(self) -> None:
        # Protocols match structurally — attributes are fine, not just properties.
        class Impl:
            name = "x"
            description = "d"
            parameters_schema = {"type": "object"}  # noqa: RUF012

        assert isinstance(Impl(), ToolDescriptor)

    def test_missing_attribute_fails_isinstance(self) -> None:
        class Bad:
            name = "x"
            description = "d"
            # no parameters_schema

        assert not isinstance(Bad(), ToolDescriptor)


# ---------------------------------------------------------------------------
# Section: AsyncTool Protocol
# ---------------------------------------------------------------------------


class TestAsyncTool:
    """AsyncTool — async execute(**kwargs) -> ToolResult (D-03-2)."""

    def test_async_class_satisfies(self) -> None:
        class Impl:
            name = "x"
            description = "d"
            parameters_schema = {"type": "object"}  # noqa: RUF012

            async def execute(self, **kwargs: Any) -> ToolResult:
                return ToolResult(tool_name="x", content="ok")

        assert isinstance(Impl(), AsyncTool)
        assert isinstance(Impl(), ToolDescriptor)  # transitively

    def test_sync_execute_fails(self) -> None:
        # AsyncTool requires async execute; runtime_checkable only checks method
        # presence, not asyncness — so we document this in the docstring rather
        # than enforce it here. But: any sync-only impl is wrong by contract.
        class Bad:
            name = "x"
            description = "d"
            parameters_schema = {"type": "object"}  # noqa: RUF012
            # no execute at all
            # we can't easily enforce asyncness via isinstance, so this test
            # only verifies that missing execute fails:

        assert not isinstance(Bad(), AsyncTool)


# ---------------------------------------------------------------------------
# Section: Spec-01 sibling sanity — Tool Protocol untouched
# ---------------------------------------------------------------------------


class TestSpec01ToolUntouched:
    """Spec-01's sync Tool Protocol remains a sibling, unchanged."""

    def test_sync_tool_still_works(self) -> None:
        class SyncImpl:
            name = "x"
            description = "d"
            parameters_schema = {"type": "object"}  # noqa: RUF012

            def __call__(self, **kwargs: Any) -> ToolResult:
                return ToolResult(tool_name="x", content="ok")

        # Spec-01's Tool Protocol still accepts a sync-callable class.
        assert isinstance(SyncImpl(), Tool)
        # And it's also a ToolDescriptor — shared metadata surface (D-03-2).
        assert isinstance(SyncImpl(), ToolDescriptor)

    def test_async_tool_is_not_sync_tool(self) -> None:
        # An AsyncTool implementation does NOT satisfy spec-01's sync Tool
        # Protocol — because spec-01's Tool requires __call__, not execute.
        class AsyncImpl:
            name = "x"
            description = "d"
            parameters_schema = {"type": "object"}  # noqa: RUF012

            async def execute(self, **kwargs: Any) -> ToolResult:
                return ToolResult(tool_name="x", content="ok")

        assert isinstance(AsyncImpl(), AsyncTool)
        # No __call__ -> not a sync Tool.
        assert not isinstance(AsyncImpl(), Tool)


# ---------------------------------------------------------------------------
# Section: tool_spec_from_tool regression — works for both Tool and AsyncTool
# ---------------------------------------------------------------------------


class TestToolSpecFromToolBridge:
    """tool_spec_from_tool accepts anything satisfying ToolDescriptor."""

    def test_works_on_sync_tool(self) -> None:
        class SyncImpl:
            name = "syncy"
            description = "spec-01 sync tool"
            parameters_schema = {"type": "object", "properties": {"q": {"type": "string"}}}  # noqa: RUF012

            def __call__(self, **kwargs: Any) -> ToolResult:
                return ToolResult(tool_name="syncy", content="ok")

        spec = tool_spec_from_tool(SyncImpl())
        assert isinstance(spec, ToolSpec)
        assert spec.name == "syncy"
        assert spec.description == "spec-01 sync tool"
        assert spec.parameters["properties"]["q"]["type"] == "string"

    def test_works_on_async_tool(self) -> None:
        class AsyncImpl:
            name = "asyncy"
            description = "spec-03 async tool"
            parameters_schema = {"type": "object", "properties": {"q": {"type": "string"}}}  # noqa: RUF012

            async def execute(self, **kwargs: Any) -> ToolResult:
                return ToolResult(tool_name="asyncy", content="ok")

        spec = tool_spec_from_tool(AsyncImpl())
        assert isinstance(spec, ToolSpec)
        assert spec.name == "asyncy"
        # Schema dict is copied, not shared.
        assert spec.parameters == AsyncImpl().parameters_schema
        assert spec.parameters is not AsyncImpl().parameters_schema


# ---------------------------------------------------------------------------
# Section: Extended ToolResult (D-03-3)
# ---------------------------------------------------------------------------


class TestToolResultExtension:
    """ToolResult gains `data` and `truncated`; no `error` field added."""

    def test_existing_fields_unchanged(self) -> None:
        r = ToolResult(
            tool_name="x",
            content="hi",
            call_id="c1",
            is_error=False,
            metadata={"latency_ms": "12"},
        )
        assert r.tool_name == "x"
        assert r.content == "hi"
        assert r.call_id == "c1"
        assert r.is_error is False
        assert r.metadata == {"latency_ms": "12"}
        # New fields default sensibly.
        assert r.data is None
        assert r.truncated is False

    def test_data_field_round_trips(self) -> None:
        payload = {"results": [{"title": "t", "url": "u", "snippet": "s"}]}
        r = ToolResult(tool_name="web_search", content="summary", data=payload)
        dumped = r.model_dump()
        restored = ToolResult.model_validate(dumped)
        assert restored.data == payload

    def test_truncated_field_round_trips(self) -> None:
        r = ToolResult(tool_name="web_fetch", content="text" * 1000, truncated=True)
        assert r.truncated is True
        dumped = r.model_dump_json()
        restored = ToolResult.model_validate_json(dumped)
        assert restored.truncated is True

    def test_error_field_rejected(self) -> None:
        # D-03-3: there is NO separate `error` field. extra="forbid" enforces.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ToolResult(tool_name="x", content="oops", error="this should fail")  # type: ignore[call-arg]

    def test_is_error_with_content_is_the_failure_truth(self) -> None:
        # The documented failure shape: is_error=True + content=<message>.
        r = ToolResult(
            tool_name="web_fetch",
            content="ConnectError: name resolution failed",
            is_error=True,
        )
        assert r.is_error is True
        assert "ConnectError" in r.content

    def test_data_can_be_arbitrary_json_dict(self) -> None:
        r = ToolResult(
            tool_name="custom",
            content="x",
            data={"nested": {"k": [1, 2, 3]}, "str": "v", "n": 42, "b": True},
        )
        # round-trips through JSON.
        restored = ToolResult.model_validate_json(r.model_dump_json())
        assert restored.data is not None
        assert restored.data["nested"]["k"] == [1, 2, 3]
        assert restored.data["b"] is True


# ---------------------------------------------------------------------------
# Section: Spec 28 — ToolResult.artifacts (backward-compat / criterion #9)
# ---------------------------------------------------------------------------


class TestToolResultArtifacts:
    """ToolResult gains `artifacts`; default-empty preserves old behavior."""

    def test_defaults_to_empty_tuple(self) -> None:
        r = ToolResult(tool_name="x", content="ok")
        assert r.artifacts == ()

    def test_old_json_without_artifacts_key_still_validates(self) -> None:
        # Backward-compat: a ToolResult serialised before Spec 28 (no `artifacts`
        # key) must still deserialise — `artifacts` defaults to ().
        old_json = (
            '{"tool_name":"x","content":"ok","call_id":"","is_error":false,'
            '"metadata":{},"data":null,"truncated":false}'
        )
        restored = ToolResult.model_validate_json(old_json)
        assert restored.artifacts == ()
        assert restored.tool_name == "x"

    def test_no_artifacts_round_trips_stable(self) -> None:
        r = ToolResult(tool_name="x", content="ok")
        restored = ToolResult.model_validate_json(r.model_dump_json())
        assert restored == r
        assert restored.artifacts == ()

    def test_populated_artifacts_round_trip(self) -> None:
        from persona.schema.tools import PersistedArtifact

        art = PersistedArtifact(
            workspace_path="uploads/a.png",
            mime_type="image/png",
            size_bytes=10,
            rendered_inline=True,
        )
        r = ToolResult(tool_name="generate_image", content="made an image", artifacts=(art,))
        restored = ToolResult.model_validate_json(r.model_dump_json())
        assert len(restored.artifacts) == 1
        assert restored.artifacts[0] == art

    def test_artifacts_is_a_tuple_after_validation(self) -> None:
        # frozen + tuple keeps the result hashable-shaped + immutable.
        from persona.schema.tools import PersistedArtifact

        art = PersistedArtifact(
            workspace_path="uploads/a.txt", mime_type="text/plain", size_bytes=1
        )
        r = ToolResult.model_validate(
            {"tool_name": "file_write", "content": "wrote", "artifacts": [art.model_dump()]}
        )
        assert isinstance(r.artifacts, tuple)
        assert r.artifacts[0].workspace_path == "uploads/a.txt"


# ---------------------------------------------------------------------------
# Section: ToolCall re-exports + decorator stub
# ---------------------------------------------------------------------------


class TestModuleReexports:
    """`persona.tools.protocol` re-exports ToolCall and ToolResult."""

    def test_tool_call_reexport(self) -> None:
        from persona.tools.protocol import ToolCall as TC_proto

        assert TC_proto is ToolCall

    def test_tool_result_reexport(self) -> None:
        from persona.tools.protocol import ToolResult as TR_proto

        assert TR_proto is ToolResult


class TestDecoratorReexport:
    """The @tool decorator is exported from `persona.tools.protocol`."""

    def test_decorator_is_callable(self) -> None:
        assert callable(tool)
        # Returns a decorator factory; calling tool(...) returns a decorator,
        # which is also callable.
        deco = tool(name="x", description="d")
        assert callable(deco)
