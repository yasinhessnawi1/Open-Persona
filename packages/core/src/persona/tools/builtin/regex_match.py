"""``regex_match`` built-in tool — ReDoS-safe pattern matching (spec 26 T03).

The ``pattern`` argument is **model-supplied** and therefore untrusted. A naive
stdlib ``re`` implementation would be exposed to catastrophic backtracking
(ReDoS), which triggers on *short* inputs — so an input-length cap is not a
sufficient guard, and Python ``re`` has no async-safe way to time out a runaway
match (D-26-X-regex-re2-undefer). This tool therefore uses **``google-re2``**
(imported as ``re2``): a linear-time automaton that is **ReDoS-immune by
construction**, so no timeout machinery is needed.

Trade-off: RE2 does not support backreferences or lookaround. Those raise a
clean ``re2.error`` that is returned as ``ToolResult(is_error=True, ...)`` with
a hint — acceptable for a match / find / replace utility tool.

Four operations (``operation`` arg): ``search`` (first match anywhere),
``fullmatch`` (whole string), ``findall`` (all matches), ``sub`` (replace).
Defense-in-depth input/pattern length caps bound memory regardless of RE2's
linear time. Every failure is returned as ``ToolResult(is_error=True)`` — never
raised (D-03-5).
"""

from __future__ import annotations

from typing import Literal

import re2

from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools.protocol import AsyncTool, tool

__all__ = ["make_regex_match_tool"]

_logger = get_logger("tools.regex_match")

_MAX_TEXT_LEN = 100_000
_MAX_PATTERN_LEN = 1_000
_MAX_FINDALL = 1_000


def _err(content: str) -> ToolResult:
    return ToolResult(tool_name="regex_match", content=content, is_error=True)


def make_regex_match_tool() -> AsyncTool:
    """Build the ``regex_match`` :class:`AsyncTool`.

    Returns:
        An :class:`AsyncTool` named ``regex_match``. Invalid patterns,
        oversized inputs, and unsupported regex features are returned as
        ``ToolResult(is_error=True)`` — never raised.
    """

    @tool(
        name="regex_match",
        description=(
            "YOU CAN match text against a regular expression. Use this tool for "
            "pattern matching / extraction / replacement instead of eyeballing "
            "the text. operation='search' finds the first match; "
            "operation='fullmatch' tests the whole string; operation='findall' "
            "returns every match; operation='sub' replaces matches with "
            "'replacement'. Uses RE2 syntax (no backreferences or lookaround)."
        ),
    )
    async def regex_match(
        pattern: str,
        text: str,
        operation: Literal["search", "fullmatch", "findall", "sub"] = "search",
        replacement: str | None = None,
        ignore_case: bool = False,
    ) -> ToolResult:
        if len(pattern) > _MAX_PATTERN_LEN:
            return _err(f"Pattern too long (> {_MAX_PATTERN_LEN} chars).")
        if len(text) > _MAX_TEXT_LEN:
            return _err(f"Input text too long (> {_MAX_TEXT_LEN} chars).")

        # RE2 takes an Options object (not re-style int flags). log_errors=False
        # silences RE2's absl stderr noise on bad patterns (we surface the error
        # via ToolResult instead); max_mem bounds the compiled-program size.
        options = re2.Options()
        options.log_errors = False
        options.case_sensitive = not ignore_case
        options.max_mem = 8 << 20  # 8 MiB compiled-program ceiling
        try:
            compiled = re2.compile(pattern, options)
        except re2.error as e:
            _logger.debug("regex_match invalid pattern", detail=str(e))
            return _err(
                f"Invalid regular expression: {e}. Note: RE2 does not support "
                "backreferences or lookaround."
            )

        if operation == "search":
            match = compiled.search(text)
            if match is None:
                return ToolResult(
                    tool_name="regex_match",
                    content="No match.",
                    data={"matched": False},
                )
            return ToolResult(
                tool_name="regex_match",
                content=f"Match: {match.group(0)!r}",
                data={
                    "matched": True,
                    "match": match.group(0),
                    "groups": list(match.groups()),
                    "span": list(match.span()),
                },
            )

        if operation == "fullmatch":
            match = compiled.fullmatch(text)
            return ToolResult(
                tool_name="regex_match",
                content="Full match." if match is not None else "No full match.",
                data={
                    "matched": match is not None,
                    "groups": list(match.groups()) if match is not None else [],
                },
            )

        if operation == "findall":
            matches = compiled.findall(text)
            truncated = len(matches) > _MAX_FINDALL
            shown = matches[:_MAX_FINDALL]
            return ToolResult(
                tool_name="regex_match",
                content=f"{len(matches)} match(es): {shown}",
                truncated=truncated,
                data={"count": len(matches), "matches": shown},
            )

        # Remaining operation is "sub".
        if replacement is None:
            return _err("'sub' requires a 'replacement' string.")
        try:
            result, count = compiled.subn(replacement, text)
        except re2.error as e:
            return _err(f"Replacement failed: {e}")
        return ToolResult(
            tool_name="regex_match",
            content=result,
            data={"result": result, "replacements": count},
        )

    return regex_match
