"""``file_write`` built-in tool — write a file to the persona's sandbox.

Creates parent directories inside the sandbox as needed. Overwrites
existing files (per spec §6.4). UTF-8 only (D-03-17).

Per Phase 1 refinement #6 + D-03-21 + D-03-25, emits one
:class:`ToolAuditEvent` per write (``action="write"``) when a
``ToolAuditLogger`` is injected at construction. The audit fires AFTER
the write succeeds (failed writes don't pollute the audit log; failures
are logged at WARNING via :mod:`persona.logging` instead).

Path resolution goes through :func:`persona.tools._sandbox.resolve_sandbox_path`.
The final open uses ``O_NOFOLLOW | O_CREAT | O_WRONLY | O_TRUNC`` to close
the TOCTOU window between the resolver's symlink check and the actual
write — a symlink swap of the final path component is rejected.
"""

from __future__ import annotations

import mimetypes
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.errors import SandboxViolationError
from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools._sandbox import resolve_sandbox_path
from persona.tools.audit import ToolAuditEvent, ToolAuditLogger
from persona.tools.protocol import AsyncTool, tool

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["make_file_write_tool"]

_logger = get_logger("tools.file_write")


def make_file_write_tool(
    *,
    sandbox_root: Path,
    audit_logger: ToolAuditLogger | None = None,
    persona_id: str | None = None,
) -> AsyncTool:
    """Build the ``file_write`` :class:`AsyncTool`.

    Args:
        sandbox_root: Per-persona working directory. The tool's path
            argument resolves against this root only.
        audit_logger: Optional tool-audit sink. If provided, every
            successful write emits one :class:`ToolAuditEvent` with
            ``action="write"`` per D-03-21.
        persona_id: Persona identifier for audit records. ``None`` for CLI
            development; audit lines then route to ``_cli.tools.jsonl``.

    Returns:
        An :class:`AsyncTool` named ``file_write`` that creates/overwrites
        files inside the sandbox. Failures return
        ``ToolResult(is_error=True, ...)`` — never raise.
    """

    @tool(
        name="file_write",
        description=(
            "Write content to a file in the working directory. "
            "Creates the file if it doesn't exist, overwrites if it does."
        ),
    )
    async def file_write(path: str, content: str) -> ToolResult:
        try:
            resolved = resolve_sandbox_path(sandbox_root, path)
        except SandboxViolationError as e:
            _logger.warning("file_write sandbox violation", requested=path, reason=str(e))
            return ToolResult(
                tool_name="file_write",
                content=f"SandboxViolationError: {e}",
                is_error=True,
            )

        # Create parent directories inside the sandbox (still within root after
        # resolver verified). mkdir is idempotent; we do not error on existing dirs.
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ToolResult(
                tool_name="file_write",
                content=f"OSError creating parent directories: {e}",
                is_error=True,
            )

        # Lone surrogates (D800-DFFF) in `content` cause UnicodeEncodeError.
        # LLMs occasionally produce these on malformed JSON; catch explicitly so
        # the model sees an informative error rather than an opaque envelope.
        # (Security review Finding 5, HIGH.)
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as e:
            return ToolResult(
                tool_name="file_write",
                content=(
                    "UnicodeEncodeError: content contains characters that "
                    f"cannot be encoded as UTF-8: {e}"
                ),
                is_error=True,
            )

        # O_NOFOLLOW closes the TOCTOU window between resolver check and open.
        # O_CREAT|O_TRUNC overwrites existing files per spec §6.4.
        # File mode 0o600 is correct for v0.1 single-user CLI; the hosted path
        # (spec 08) uses Postgres-backed storage and does not produce local files.
        try:
            fd = os.open(
                resolved,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
        except PermissionError as e:
            return ToolResult(
                tool_name="file_write",
                content=f"PermissionError: {e}",
                is_error=True,
            )
        except OSError as e:
            # ELOOP from O_NOFOLLOW when path is a symlink; also other OS errors.
            return ToolResult(
                tool_name="file_write",
                content=f"OSError: {e}",
                is_error=True,
            )

        # os.write on a regular file is all-or-nothing per POSIX (no looping
        # required), but it can still raise OSError (ENOSPC, EIO, ...) which
        # must not escape the @tool envelope opaquely. (Security review Finding 10.2.)
        try:
            try:
                os.write(fd, encoded)
            except OSError as e:
                return ToolResult(
                    tool_name="file_write",
                    content=f"OSError writing file: {e}",
                    is_error=True,
                )
        finally:
            os.close(fd)

        # Audit the successful write (D-03-21, D-03-25). Failed writes already
        # returned above.
        if audit_logger is not None:
            audit_logger.emit(
                ToolAuditEvent(
                    timestamp=datetime.now(UTC),
                    persona_id=persona_id,
                    tool_name="file_write",
                    action="write",
                    resource=path,
                    metadata={"bytes": str(len(encoded))},
                )
            )

        # Populate produced_files for parity with sandbox/tool.py's code_execution
        # result shape, so downstream consumers (Spec 19 file-output surfaces) can
        # treat both code-execution outputs and direct writes uniformly.
        media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        produced_files = [
            {
                "path": path,
                "size_bytes": str(len(encoded)),
                "media_type": media_type,
            }
        ]
        return ToolResult(
            tool_name="file_write",
            content=f"Wrote {len(encoded)} bytes to {path}",
            data={
                "path": path,
                "bytes_written": str(len(encoded)),
                "produced_files": produced_files,
            },
        )

    return file_write
