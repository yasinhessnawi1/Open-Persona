"""``file_read`` built-in tool — read a file from the persona's sandbox.

Path resolution goes through :func:`persona.tools._sandbox.resolve_sandbox_path`
which rejects `..`, absolute paths, NULL bytes, symlinks escaping the
sandbox, and pathological inputs (D-03-13..D-03-15).

UTF-8 with ``errors="replace"`` (D-03-17 — no chardet in v0.1). Files
larger than 1 MB are truncated and the result's ``truncated`` flag is set
(D-03-3, D-03-16). Per the T09 security-reviewer recommendation, the open
call uses ``O_NOFOLLOW`` to close the TOCTOU window between the resolver's
symlink check and this open() — a symlink swap at the final path component
between the two operations is rejected.

Failures (sandbox violation, missing file, permission error) are returned
as ``ToolResult(is_error=True, content=...)`` via the ``@tool`` decorator's
no-raise envelope. ``file_read`` does NOT emit audit events (read-only;
D-03-21).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from persona.errors import SandboxViolationError
from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools._sandbox import resolve_sandbox_path
from persona.tools.protocol import AsyncTool, tool

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["make_file_read_tool"]

_logger = get_logger("tools.file_read")

_MAX_BYTES = 1_048_576  # 1 MB (D-03-16)


def make_file_read_tool(*, sandbox_root: Path) -> AsyncTool:
    """Build the ``file_read`` :class:`AsyncTool`.

    Args:
        sandbox_root: Per-persona working directory. The tool's path
            argument resolves against this root only — no escape possible.

    Returns:
        An :class:`AsyncTool` named ``file_read`` that reads UTF-8 text from
        files inside the sandbox. Failures return
        ``ToolResult(is_error=True, ...)`` — never raise.
    """

    @tool(
        name="file_read",
        description=(
            "YOU CAN read files. Use this tool whenever the user asks about a "
            "file's contents in the working directory — do not say you cannot "
            "access files: call this tool."
        ),
    )
    async def file_read(path: str) -> ToolResult:
        try:
            resolved = resolve_sandbox_path(sandbox_root, path)
        except SandboxViolationError as e:
            _logger.warning("file_read sandbox violation", requested=path, reason=str(e))
            return ToolResult(
                tool_name="file_read",
                content=f"SandboxViolationError: {e}",
                is_error=True,
            )

        # O_NOFOLLOW closes the TOCTOU window between resolver's symlink check
        # and this open() — a swap of the final path component to a symlink
        # between the two operations is rejected (security review T09).
        try:
            fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return ToolResult(
                tool_name="file_read",
                content=f"FileNotFoundError: {path}",
                is_error=True,
            )
        except IsADirectoryError:
            return ToolResult(
                tool_name="file_read",
                content=f"IsADirectoryError: {path} is a directory, not a file",
                is_error=True,
            )
        except PermissionError as e:
            return ToolResult(
                tool_name="file_read",
                content=f"PermissionError: {e}",
                is_error=True,
            )
        except OSError as e:
            # ELOOP from O_NOFOLLOW on a symlink; also covers other OS errors.
            return ToolResult(
                tool_name="file_read",
                content=f"OSError: {e}",
                is_error=True,
            )

        try:
            raw = os.read(fd, _MAX_BYTES + 1)  # read one extra byte to detect overflow
        finally:
            os.close(fd)

        truncated = len(raw) > _MAX_BYTES
        if truncated:
            raw = raw[:_MAX_BYTES]
        text = raw.decode("utf-8", errors="replace")

        return ToolResult(
            tool_name="file_read",
            content=text,
            truncated=truncated,
            data={
                "path": path,
                "bytes_read": str(len(raw)),
                "encoding": "utf-8",
            },
        )

    return file_read
