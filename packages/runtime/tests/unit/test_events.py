"""Unit tests for persona_runtime.agentic.events (T03, D-06-1).

Each typed constructor produces the right `type` string + `data` payload, the
timestamp is tz-aware, and `model_dump_json` round-trips (the API serialises
these to SSE — spec §8, acceptance #9).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.schema.tools import ToolCall, ToolResult
from persona_runtime.agentic.events import RunEvent
from persona_runtime.agentic.run import Run, RunStatus
from pydantic import ValidationError


class TestStartedAndSimpleEvents:
    def test_started(self) -> None:
        ev = RunEvent.started("draft a letter")
        assert ev.type == "started"
        assert ev.step == -1
        assert ev.data == {"task": "draft a letter"}

    def test_thinking(self) -> None:
        ev = RunEvent.thinking(2)
        assert ev.type == "thinking"
        assert ev.step == 2
        assert ev.data == {}

    def test_user_responded(self) -> None:
        ev = RunEvent.user_responded(3)
        assert ev.type == "user_responded"
        assert ev.step == 3

    def test_cancelled(self) -> None:
        ev = RunEvent.cancelled(5)
        assert ev.type == "cancelled"
        assert ev.step == 5


class TestToolEvents:
    def test_tool_calling_renders_calls_json_safe(self) -> None:
        calls = [
            ToolCall(name="web_search", args={"query": "mould"}, call_id="c-1"),
            ToolCall(name="web_fetch", args={"url": "http://x"}, call_id="c-2"),
        ]
        ev = RunEvent.tool_calling(1, calls)
        assert ev.type == "tool_calling"
        assert ev.data["tool_names"] == "web_search, web_fetch"
        assert ev.data["tool_calls"][0] == {
            "name": "web_search",
            "call_id": "c-1",
            "args": {"query": "mould"},
        }

    def test_tool_result_success(self) -> None:
        result = ToolResult(tool_name="web_search", content="3 hits", call_id="c-1")
        ev = RunEvent.tool_result(1, "web_search", result)
        assert ev.type == "tool_result"
        assert ev.data == {"tool_name": "web_search", "is_error": False, "content": "3 hits"}

    def test_tool_result_error(self) -> None:
        result = ToolResult(tool_name="bogus", content="not available", is_error=True)
        ev = RunEvent.tool_result(2, "bogus", result)
        assert ev.data["is_error"] is True
        assert ev.data["content"] == "not available"


class TestActionEvents:
    def test_asking_user(self) -> None:
        ev = RunEvent.asking_user(4, "Which apartment?")
        assert ev.type == "asking_user"
        assert ev.data == {"question": "Which apartment?"}

    def test_reasoning(self) -> None:
        ev = RunEvent.reasoning(2, "let me think")
        assert ev.type == "reasoning"
        assert ev.data == {"content": "let me think"}

    def test_completed(self) -> None:
        ev = RunEvent.completed(6, "the final letter")
        assert ev.type == "completed"
        assert ev.data == {"output": "the final letter"}

    def test_max_steps(self) -> None:
        ev = RunEvent.max_steps(20, "got this far")
        assert ev.type == "max_steps"
        assert ev.data == {"summary": "got this far"}

    def test_error(self) -> None:
        ev = RunEvent.error(3, "provider 500")
        assert ev.type == "error"
        assert ev.data == {"message": "provider 500"}

    def test_finished(self) -> None:
        run = Run(
            id="run-1",
            persona_id="astrid",
            task="t",
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC),
        )
        ev = RunEvent.finished(run)
        assert ev.type == "finished"
        assert ev.data == {"run_id": "run-1", "status": "completed"}


class TestRunEventInvariants:
    def test_timestamp_is_tz_aware(self) -> None:
        ev = RunEvent.started("t")
        assert ev.timestamp.tzinfo is not None

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RunEvent(type="started", step=-1, data={}, timestamp=datetime(2026, 5, 28))  # noqa: DTZ001

    def test_frozen(self) -> None:
        ev = RunEvent.thinking(1)
        with pytest.raises(ValidationError):
            ev.type = "x"  # type: ignore[misc]

    def test_json_round_trip(self) -> None:
        calls = [ToolCall(name="echo", args={"text": "hi"}, call_id="c-1")]
        ev = RunEvent.tool_calling(1, calls)
        restored = RunEvent.model_validate_json(ev.model_dump_json())
        assert restored == ev


class TestToolResultProducedFilesForwarding:
    """Spec F4 T02b — additive ``produced_files`` forwarding on tool_result.

    Verifies the D-F4-X-event-kind-for-produced-files lock (Option A: edit
    one constructor, no Pydantic schema change). The single constructor at
    ``events.py:96-103`` serves BOTH chat SSE (bare payload via
    ``_sse(ev.type, ev.data)``) AND run SSE (whole RunEvent envelope via
    ``event.model_dump_json()``) per the module-level docstring at
    events.py:7-8 — one constructor edit lights up both transports.
    """

    def test_payload_back_compat_when_data_is_none(self) -> None:
        """``ToolResult.data is None`` → payload retains the three-key shape.

        Back-compat: existing consumers see exactly ``{tool_name, is_error,
        content}`` — the addition is silent until a tool surfaces
        produced_files.
        """
        result = ToolResult(tool_name="web_search", content="ok", is_error=False)
        event = RunEvent.tool_result(step=0, tool_name="web_search", result=result)
        assert event.data == {
            "tool_name": "web_search",
            "is_error": False,
            "content": "ok",
        }
        assert "produced_files" not in event.data

    def test_payload_back_compat_when_data_omits_produced_files(self) -> None:
        """Tools whose ``.data`` carries OTHER structured detail (truncated,
        results) do not contribute produced_files; the field stays absent."""
        result = ToolResult(
            tool_name="file_read",
            content="ok",
            is_error=False,
            data={"truncated": True},
        )
        event = RunEvent.tool_result(step=0, tool_name="file_read", result=result)
        assert "produced_files" not in event.data

    def test_produced_files_forwarded_when_present(self) -> None:
        """Non-empty list under ``data["produced_files"]`` is forwarded verbatim.

        Mirrors what the sandbox tool factory populates at
        ``packages/core/src/persona/sandbox/tool.py:269-279`` — list of
        ``{path, size_bytes, media_type}`` dicts.
        """
        produced = [
            {"path": "charts/q1.png", "size_bytes": 12345, "media_type": "image/png"},
            {
                "path": "report.docx",
                "size_bytes": 67890,
                "media_type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
            },
        ]
        result = ToolResult(
            tool_name="code_execution",
            content="ok",
            is_error=False,
            data={"produced_files": produced},
        )
        event = RunEvent.tool_result(step=1, tool_name="code_execution", result=result)
        assert event.data["produced_files"] == produced
        assert event.data["tool_name"] == "code_execution"
        assert event.data["is_error"] is False
        assert event.data["content"] == "ok"

    def test_empty_produced_files_list_is_omitted(self) -> None:
        """Empty ``produced_files: []`` is omitted from the payload.

        Absence IS the back-compat shape; renderers treat absence as "no
        files to render." This avoids emitting noise frames when a sandbox
        dispatch happened to produce zero files.
        """
        result = ToolResult(
            tool_name="code_execution",
            content="ok",
            is_error=False,
            data={"produced_files": []},
        )
        event = RunEvent.tool_result(step=0, tool_name="code_execution", result=result)
        assert "produced_files" not in event.data

    def test_is_error_path_carries_no_produced_files(self) -> None:
        """``ProducedFileSizeError`` + other failures: ``is_error=True``; data
        may contain ``error_type`` / ``context`` but not produced_files.

        The event surfaces ``is_error=True`` so the renderer routes to a
        failure variant via the normaliser; absence of produced_files
        prevents accidental file-card render alongside the failure.
        """
        result = ToolResult(
            tool_name="code_execution",
            content="produced file exceeds 100 MB cap",
            is_error=True,
            data={"error_type": "ProducedFileSizeError"},
        )
        event = RunEvent.tool_result(step=0, tool_name="code_execution", result=result)
        assert event.data["is_error"] is True
        assert "produced_files" not in event.data

    def test_json_round_trip_preserves_produced_files(self) -> None:
        """The forwarded structure survives ``model_dump_json`` / reload.

        Run SSE serialises via ``event.model_dump_json()`` (D-09-1 nested
        ``.data`` envelope); chat SSE serialises via
        ``json.dumps(ev.data)`` (D-09-1 bare payload). Both paths require
        the list to be JSON-safe — verified here.
        """
        produced = [
            {"path": "charts/x.png", "size_bytes": 100, "media_type": "image/png"},
        ]
        result = ToolResult(
            tool_name="code_execution",
            content="ok",
            is_error=False,
            data={"produced_files": produced},
        )
        event = RunEvent.tool_result(step=2, tool_name="code_execution", result=result)
        restored = RunEvent.model_validate_json(event.model_dump_json())
        assert restored == event
        assert restored.data["produced_files"] == produced
