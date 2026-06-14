"""``json_query`` built-in tool — JMESPath queries over JSON (spec 26 T05).

Lets a persona extract a field / slice / projection from a JSON document
without falling back to ``code_execution``. Uses **JMESPath** (the AWS-CLI query
language; pure-Python, zero transitive deps — D-26-X-launch-deps).

``json_input`` is JSON *text* (e.g. a prior tool result); ``query`` is a
JMESPath expression. A query that matches nothing returns ``null`` (JMESPath
semantics), which is reported as a clean "no match", not an error. Invalid JSON
and invalid queries are returned as ``ToolResult(is_error=True, content=...)`` —
never raised (D-03-5).
"""

from __future__ import annotations

import json
from typing import Any

import jmespath
from jmespath.exceptions import JMESPathError

from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools.protocol import AsyncTool, tool

__all__ = ["make_json_query_tool"]

_logger = get_logger("tools.json_query")

_MAX_INPUT_LEN = 1_000_000
_MAX_CONTENT_LEN = 8_000


def _err(content: str) -> ToolResult:
    return ToolResult(tool_name="json_query", content=content, is_error=True)


def make_json_query_tool() -> AsyncTool:
    """Build the ``json_query`` :class:`AsyncTool`.

    Returns:
        An :class:`AsyncTool` named ``json_query``. Invalid JSON / queries are
        returned as ``ToolResult(is_error=True)`` — never raised.
    """

    @tool(
        name="json_query",
        description=(
            "YOU CAN query JSON data with a JMESPath expression. Use this tool "
            "to pull a field, slice, or projection out of a JSON document "
            "instead of parsing it by hand. Provide json_input (the JSON text) "
            "and query (a JMESPath expression, e.g. 'items[0].name', "
            "'results[].id', 'a.b.c')."
        ),
    )
    async def json_query(json_input: str, query: str) -> ToolResult:
        if len(json_input) > _MAX_INPUT_LEN:
            return _err(f"JSON input too large (> {_MAX_INPUT_LEN} chars).")
        try:
            data = json.loads(json_input)
        except (json.JSONDecodeError, ValueError) as e:
            return _err(f"Invalid JSON input: {e}")

        try:
            result: Any = jmespath.search(query, data)
        except JMESPathError as e:
            _logger.debug("json_query invalid expression", detail=str(e))
            return _err(f"Invalid JMESPath query: {e}")

        if result is None:
            return ToolResult(
                tool_name="json_query",
                content="No match (query returned null).",
                data={"matched": False, "result": None},
            )

        rendered = json.dumps(result, ensure_ascii=False, default=str)
        truncated = len(rendered) > _MAX_CONTENT_LEN
        return ToolResult(
            tool_name="json_query",
            content=rendered[:_MAX_CONTENT_LEN],
            truncated=truncated,
            data={"matched": True, "result": result},
        )

    return json_query
