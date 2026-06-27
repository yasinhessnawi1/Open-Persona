"""Redaction — the safe args-summary boundary for activity events (P2-D-2).

The activity-start event (P2) carries a summary of a tool call's arguments so the UI
can show "using <X>…" with context and the persisted trail records what ran. Secrets
must never leak into that summary (acceptance criterion 5 — the security gate).

Finding (research §2): credentials do **not** flow through ``ToolCall.args`` — MCP
server credentials are bound at connection time as client headers, and tool API keys
are injected into the tool instance at composition time; per-call ``args`` carry
user-supplied inputs (a query, a path, code). So this redactor is **defence-in-depth**:

1. a **key denylist** (applied at every nesting level) catches the rare
   secret-shaped key that slips into args, and
2. **value truncation + a total cap** are the real backstop — a denylist is inherently
   leaky (a novel sensitive key slips through), and the cap is what bounds exposure when
   the denylist misses. Free-text *values* that happen to contain a short secret are an
   accepted residual risk (they are user inputs); truncation still bounds them.

The output is a flat ``dict[str, str]`` — JSON-safe by construction, so it serialises
cleanly onto the event payload (mirrors the events.py JSON-safe discipline).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["REDACTED", "redact_args"]

#: The placeholder substituted for a value whose key matched the denylist.
REDACTED = "‹redacted›"  # ‹redacted›

#: Case-insensitive substrings that mark a key as carrying a secret. Deliberately
#: specific (no bare ``"key"``/``"auth"``) so benign args like ``keyword`` are not
#: over-redacted — the value truncation + total cap below are the real safety net.
_DENY_SUBSTRINGS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "authorization",
    "bearer",
    "cookie",
)

_VALUE_MAX_CHARS = 120
_TOTAL_MAX_CHARS = 512
_TRUNCATION_MARKER = "…"  # …


def _is_sensitive_key(key: str) -> bool:
    """True if ``key`` looks like it carries a secret (denylist substring match)."""
    lowered = key.lower()
    return any(sub in lowered for sub in _DENY_SUBSTRINGS)


def _redact_structure(value: Any) -> Any:  # noqa: ANN401 — args are JSON-shaped, arbitrary
    """Recursively replace values under sensitive keys, preserving structure.

    Catches a secret nested inside a structured arg (e.g.
    ``{"headers": {"Authorization": "Bearer …"}}``) before the value is summarised.
    """
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_sensitive_key(str(k)) else _redact_structure(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_structure(v) for v in value]
    return value


def _summarise_value(value: Any) -> str:  # noqa: ANN401 — args are JSON-shaped, arbitrary
    """Render a (nested-redacted) value to a bounded, JSON-safe string."""
    redacted = _redact_structure(value)
    if isinstance(redacted, str):
        text = redacted
    else:
        try:
            text = json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = repr(redacted)
    if len(text) > _VALUE_MAX_CHARS:
        overflow = len(text) - _VALUE_MAX_CHARS
        return f"{text[:_VALUE_MAX_CHARS]}{_TRUNCATION_MARKER} (+{overflow} chars)"
    return text


def _enforce_total_cap(summary: dict[str, str]) -> dict[str, str]:
    """Bound the whole summary's serialised size, dropping the largest values first.

    Deterministic: keys are dropped by (value length, key) so the same input always
    yields the same capped output. A ``…`` marker records that truncation occurred.
    """

    def _size(d: dict[str, str]) -> int:
        return len(json.dumps(d, ensure_ascii=False, sort_keys=True))

    if _size(summary) <= _TOTAL_MAX_CHARS:
        return summary

    work = dict(summary)
    work[_TRUNCATION_MARKER] = "(args summary truncated)"
    while _size(work) > _TOTAL_MAX_CHARS and len(work) > 1:
        candidates = [(len(v), k) for k, v in work.items() if k != _TRUNCATION_MARKER]
        if not candidates:
            break
        candidates.sort(reverse=True)
        _, drop_key = candidates[0]
        del work[drop_key]
    return work


def redact_args(args: Mapping[str, Any]) -> dict[str, str]:
    """Return a redacted, bounded, JSON-safe summary of tool-call arguments.

    Top-level keys matching the denylist are replaced wholesale; every other value is
    recursively nested-redacted, stringified, and truncated to ``_VALUE_MAX_CHARS``;
    the whole summary is then capped at ``_TOTAL_MAX_CHARS``. Keys are processed sorted
    so the output is deterministic.

    Args:
        args: The tool call's ``args`` mapping (user-supplied, untrusted).

    Returns:
        A flat ``dict[str, str]`` safe to place on an activity-start event.
    """
    summary: dict[str, str] = {}
    for key in sorted(args):
        if _is_sensitive_key(key):
            summary[key] = REDACTED
        else:
            summary[key] = _summarise_value(args[key])
    return _enforce_total_cap(summary)
