"""Sandbox path resolver for the file_read / file_write built-ins (T09).

Pure function. No I/O, no ``os.access``, no read/write. The caller (file
tools in T10) opens the resolved path; we only verify the path *would* be
inside the sandbox root after symlink resolution.

Validation order (research.md §5.2 — D-03-13, D-03-14, D-03-15):
1. NULL byte → reject (stdlib raises ``ValueError`` from .resolve() — we
   want our own domain error before the bytes touch ``Path``).
2. Length cap > 4096 chars → reject (stdlib doesn't bound; DOS protection).
3. Backslash on POSIX (``os.sep == "/"``) → reject (defense in depth;
   ``a\\b\\c`` is a single weird filename on POSIX, operator-confusing).
4. Empty / whitespace-only → reject (file tools never want "the directory
   itself"; D-03-13).
5. Absolute path (``os.path.isabs``) → reject.
6. ``Path.resolve(strict=False)`` + ``is_relative_to(root.resolve())`` →
   the inner symlink/traversal check (D-03-14).

Returns the resolved absolute ``Path`` (which may not yet exist —
``file_write`` creates new files). Callers are responsible for opening it.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from persona.errors import SandboxViolationError

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["resolve_sandbox_path"]

_MAX_PATH_LENGTH = 4096

# Consistent recovery hint appended to every SandboxViolationError message
# (T10 / D-25-5 / spec §2.5). The model that triggered the violation reads the
# ToolResult text and needs a concrete, valid path form to retry with — not
# just a statement of what was wrong. Every reason ends with the SAME relative
# path example so the recovery action is unambiguous regardless of which check
# fired. ``<root>`` stands for the sandbox root, whose absolute value the model
# neither knows nor needs (it is per-persona); the example shows the *form* of
# a relative path that resolves inside it.
_VALID_PATH_HINT = (
    "use a relative path like 'out/report.md' "
    "(resolves to <root>/out/report.md under the sandbox root)"
)


def _violation_message(summary: str, reason: str) -> str:
    """Compose a model-recoverable SandboxViolationError message.

    Args:
        summary: Human-readable statement of what was wrong (no trailing
            punctuation).
        reason: The machine discriminator (mirrors ``context["reason"]``) so
            the model sees the same token in the prose that it does in the
            structured context.

    Returns:
        ``"<summary> [reason=<reason>]; <valid-path hint>"`` — a string that
        tells the model both what failed and a concrete relative path form
        that would succeed, enabling a recovery retry instead of giving up.
    """
    return f"{summary} [reason={reason}]; {_VALID_PATH_HINT}"


def resolve_sandbox_path(root: Path, requested: str) -> Path:
    """Resolve ``requested`` against ``root``; reject any escape attempts.

    Args:
        root: Sandbox root. Must exist or be createable by the caller;
            we resolve it via :meth:`Path.resolve` to canonicalise.
        requested: Caller-supplied path (from a tool argument). May be
            absolute, contain ``..``, NULL bytes, etc. — all rejected.

    Returns:
        An absolute :class:`Path` guaranteed to be inside ``root.resolve()``.
        The path may not yet exist (``file_write`` creates new files).

    Raises:
        SandboxViolationError: If the requested path escapes the sandbox
            or fails any of the validation checks listed in the module
            docstring. The exception's ``context`` carries the offending
            input (control-char-stripped and truncated) and a ``reason``
            discriminator.

    Note:
        Callers should open the returned path with ``os.O_NOFOLLOW`` (or
        ``open(..., opener=...)`` wrapping ``os.open`` with that flag) to
        close the narrow TOCTOU window between this resolver's symlink
        check and the actual file open. See spec-03 decision D-03-14 for
        the full risk-acceptance rationale (single-tenant CLI scope in v0.1;
        post-September multi-tenant hardening is spec 11).
    """
    # Pre-Path validation (cheap; runs first so the stdlib doesn't bite us).

    if "\x00" in requested:
        raise SandboxViolationError(
            _violation_message("null byte in path", "null_byte"),
            context={"reason": "null_byte", "requested_preview": _preview(requested)},
        )

    if len(requested) > _MAX_PATH_LENGTH:
        raise SandboxViolationError(
            _violation_message("path too long", "too_long"),
            context={"reason": "too_long", "length": str(len(requested))},
        )

    if os.sep == "/" and "\\" in requested:
        raise SandboxViolationError(
            _violation_message("windows-style separator on POSIX", "mixed_separators"),
            context={"reason": "mixed_separators", "requested": _preview(requested)},
        )

    if not requested or requested.strip() == "":
        raise SandboxViolationError(
            _violation_message("empty path", "empty"),
            context={"reason": "empty"},
        )

    # PurePosixPath gives deterministic behavior regardless of host separator;
    # we already rejected backslash on POSIX above.
    if PurePosixPath(requested).is_absolute():
        raise SandboxViolationError(
            _violation_message("absolute path not allowed", "absolute"),
            context={"reason": "absolute", "requested": _preview(requested)},
        )

    # Reject paths that resolve to the sandbox root itself (D-03-13 spirit:
    # file_read/file_write never want "the directory itself" as a target).
    # PurePosixPath normalizes "." and "./" to a path with empty parts;
    # we also catch "." with surrounding whitespace explicitly.
    pure = PurePosixPath(requested)
    if pure.parts == () or pure.parts == (".",) or requested.strip() in (".", "./"):
        raise SandboxViolationError(
            _violation_message("path resolves to sandbox root directory", "root_reference"),
            context={"reason": "root_reference", "requested": _preview(requested)},
        )

    # Path + symlink resolution. strict=False because the caller's target
    # may not exist yet (file_write creates new files).
    root_resolved = root.resolve(strict=False)
    candidate = (root / requested).resolve(strict=False)

    if not candidate.is_relative_to(root_resolved):
        raise SandboxViolationError(
            _violation_message("path escapes sandbox", "escape"),
            context={
                "reason": "escape",
                "requested": _preview(requested),
                "resolved": _preview(str(candidate)),
            },
        )

    return candidate


def _preview(s: str, *, max_len: int = 120) -> str:
    """Make a user-supplied string safe for inclusion in audit-log context.

    Strips ASCII control characters (notably NUL, which would survive into
    JSONL audit lines as a literal ``\\u0000`` and break some downstream
    log shippers / SIEM parsers). Preserves printable Unicode (including
    non-Latin scripts) and TAB. Truncates to ``max_len`` characters.
    """
    cleaned_chars = [c for c in s if c == "\t" or ord(c) >= 32]
    cleaned = "".join(cleaned_chars)
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len] + "...<truncated>"
