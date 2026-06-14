"""``text_diff`` built-in tool — unified diff between two texts (spec 26 T06).

Pure stdlib (:func:`difflib.unified_diff`). Lets a persona show exactly what
changed between two versions of a text instead of describing it prose-style.

``difflib.SequenceMatcher`` is worst-case quadratic, so inputs are size-capped
(line count + bytes) before diffing; binary/undecodable content (NUL bytes) is
rejected. Every guard violation is returned as ``ToolResult(is_error=True)`` —
never raised (D-03-5).
"""

from __future__ import annotations

import difflib

from persona.schema.tools import ToolResult
from persona.tools.protocol import AsyncTool, tool

__all__ = ["make_text_diff_tool"]

_MAX_CHARS = 200_000
_MAX_LINES = 20_000
_MAX_CONTENT_LEN = 12_000


def _err(content: str) -> ToolResult:
    return ToolResult(tool_name="text_diff", content=content, is_error=True)


def make_text_diff_tool() -> AsyncTool:
    """Build the ``text_diff`` :class:`AsyncTool`.

    Returns:
        An :class:`AsyncTool` named ``text_diff``. Oversized or binary inputs
        are returned as ``ToolResult(is_error=True)`` — never raised.
    """

    @tool(
        name="text_diff",
        description=(
            "YOU CAN compute the exact differences between two texts. Use this "
            "tool to show what changed between an old and a new version "
            "(line-by-line unified diff) instead of describing the changes. "
            "Provide 'a' (original) and 'b' (revised); optional 'label_a'/"
            "'label_b' name the sides."
        ),
    )
    async def text_diff(
        a: str,
        b: str,
        label_a: str = "original",
        label_b: str = "revised",
        context_lines: int = 3,
    ) -> ToolResult:
        if "\x00" in a or "\x00" in b:
            return _err("Binary input not supported; provide UTF-8 text.")
        if len(a) > _MAX_CHARS or len(b) > _MAX_CHARS:
            return _err(f"Input too large (> {_MAX_CHARS} chars per side).")
        a_lines = a.splitlines()
        b_lines = b.splitlines()
        if len(a_lines) > _MAX_LINES or len(b_lines) > _MAX_LINES:
            return _err(f"Too many lines (> {_MAX_LINES} per side).")

        diff_lines = list(
            difflib.unified_diff(
                a_lines,
                b_lines,
                fromfile=label_a,
                tofile=label_b,
                lineterm="",
                n=max(0, context_lines),
            )
        )
        if not diff_lines:
            return ToolResult(
                tool_name="text_diff",
                content="No differences.",
                data={"changed": False, "added": 0, "removed": 0},
            )

        # Count content changes (exclude the +++/--- file headers).
        added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
        rendered = "\n".join(diff_lines)
        truncated = len(rendered) > _MAX_CONTENT_LEN
        return ToolResult(
            tool_name="text_diff",
            content=rendered[:_MAX_CONTENT_LEN],
            truncated=truncated,
            data={"changed": True, "added": added, "removed": removed},
        )

    return text_diff
