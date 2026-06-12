"""Sandbox error-UX tests: every violation message carries a valid-path hint.

T10 / D-25-5 / spec §2.5 (acceptance criteria 4 + 8). The original
``SandboxViolationError`` messages stated only what was WRONG; a model that
hit one had no way to construct a path that WOULD work and often gave up.
Each of the 7 raise sites (null_byte, too_long, mixed_separators, empty,
absolute, root_reference, escape) must now append a consistent relative-path
example so the model can recover.

These tests assert two things per reason:
1. the human-readable message contains a relative-path example, and
2. the message names the reason discriminator,
while the structured ``context`` dict (reason + preview) is left intact.

The final test exercises the pass-through: a ``file_write`` call with an
absolute path returns a ToolResult whose ``content`` includes the hint, so the
model actually sees it.
"""

# ruff: noqa: ANN401, ARG001, ARG002, ERA001
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona.errors import SandboxViolationError
from persona.tools._sandbox import resolve_sandbox_path
from persona.tools.builtin.file_write import make_file_write_tool

if TYPE_CHECKING:
    from pathlib import Path

# One representative bad input per reason. Each MUST trip exactly the reason
# named in the second tuple element (verified via context["reason"]).
_REASON_CASES: list[tuple[str, str]] = [
    ("\x00", "null_byte"),
    ("a" * (4096 + 1), "too_long"),
    ("a\\b\\c", "mixed_separators"),
    ("   ", "empty"),
    ("/workspace/out/report.md", "absolute"),
    (".", "root_reference"),
    ("../escape.txt", "escape"),
]

# Human-readable summary the resolver pairs with each reason (the "what was
# wrong" half of the message, before the shared hint).
_SUMMARY_BY_REASON: dict[str, str] = {
    "null_byte": "null byte in path",
    "too_long": "path too long",
    "mixed_separators": "windows-style separator on POSIX",
    "empty": "empty path",
    "absolute": "absolute path not allowed",
    "root_reference": "path resolves to sandbox root directory",
    "escape": "path escapes sandbox",
}


def _summary_for(reason: str) -> str:
    return _SUMMARY_BY_REASON[reason]


class TestPathHintInMessage:
    """Every raise site enriches its message with a recoverable path example."""

    @pytest.mark.parametrize(("bad", "reason"), _REASON_CASES)
    def test_message_contains_relative_path_example(
        self, tmp_path: Path, bad: str, reason: str
    ) -> None:
        with pytest.raises(SandboxViolationError) as exc_info:
            resolve_sandbox_path(tmp_path, bad)
        msg = str(exc_info.value)
        # The relative-path example the model can copy to recover.
        assert "out/report.md" in msg
        assert "relative path" in msg
        # The hint points the model at the sandbox-root resolution form.
        assert "sandbox root" in msg

    @pytest.mark.parametrize(("bad", "reason"), _REASON_CASES)
    def test_message_names_the_reason(self, tmp_path: Path, bad: str, reason: str) -> None:
        with pytest.raises(SandboxViolationError) as exc_info:
            resolve_sandbox_path(tmp_path, bad)
        assert f"[reason={reason}]" in str(exc_info.value)

    @pytest.mark.parametrize(("bad", "reason"), _REASON_CASES)
    def test_context_dict_shape_preserved(self, tmp_path: Path, bad: str, reason: str) -> None:
        # The enrichment touches only the human-readable message string; the
        # structured context (reason + preview-style fields) is unchanged.
        with pytest.raises(SandboxViolationError) as exc_info:
            resolve_sandbox_path(tmp_path, bad)
        ctx = exc_info.value.context
        assert ctx.get("reason") == reason

    def test_hint_is_consistent_across_all_reasons(self, tmp_path: Path) -> None:
        # The valid-path example must be identical for every reason so the
        # model's recovery action does not depend on which check fired. We read
        # ``args[0]`` (the raw message passed to the exception) rather than
        # ``str(exc)`` — PersonaError appends the structured context to ``str``,
        # which legitimately varies per reason.
        shared_hint = (
            "use a relative path like 'out/report.md' "
            "(resolves to <root>/out/report.md under the sandbox root)"
        )
        for bad, reason in _REASON_CASES:
            with pytest.raises(SandboxViolationError) as exc_info:
                resolve_sandbox_path(tmp_path, bad)
            raw_message = exc_info.value.args[0]
            assert raw_message == f"{_summary_for(reason)} [reason={reason}]; {shared_hint}"


class TestFileWriteSurfacesHint:
    """The enriched message reaches the model via the ToolResult content."""

    @pytest.mark.asyncio
    async def test_absolute_path_error_includes_hint(self, tmp_path: Path) -> None:
        tool_inst = make_file_write_tool(sandbox_root=tmp_path)
        result = await tool_inst.execute(
            path="/workspace/out/startup_launch_funnel.md", content="x"
        )
        assert result.is_error is True
        # The model sees the hint embedded in the tool result content.
        assert "out/report.md" in result.content
        assert "relative path" in result.content
        assert "[reason=absolute]" in result.content
