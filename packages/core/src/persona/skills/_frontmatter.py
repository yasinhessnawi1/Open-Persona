"""SKILL.md front-matter parser (T03, D-04-3).

Hand-rolled ~25-LOC parser over ``yaml.safe_load`` (PyYAML is already a core
dep; declared in ``packages/core/pyproject.toml``). Replaces the candidate
``python-frontmatter`` library — Phase 3 research §2 found the library
silently returns empty metadata on UTF-8 BOM-prefixed valid SKILL.md and
three other malformed cases. The scanner (T04) needs a typed signal for
its warn-and-skip envelope; the library doesn't provide one.

Failure modes (all raise :class:`persona.errors.SkillManifestError` with
structured ``context``):

- missing opening delimiter (``---\\n`` not at start, after BOM strip)
- missing closing delimiter (no ``\\n---\\n`` found in the remainder)
- malformed YAML inside the front matter
- front matter is not a YAML mapping (e.g., a top-level list or scalar)

Tolerates: UTF-8 BOM at start (stripped), CRLF line endings (normalised to
LF before delimiter detection).
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — runtime import
from typing import Any

import yaml

from persona.errors import SkillManifestError

__all__ = ["parse_skill_markdown"]


def parse_skill_markdown(path: Path) -> tuple[dict[str, Any], str]:
    """Parse a SKILL.md file into ``(front_matter_dict, body_text)``.

    Args:
        path: Filesystem path to the SKILL.md file. Must be readable.

    Returns:
        A ``(meta, body)`` tuple where ``meta`` is the front-matter dict
        (Pydantic validates field shapes downstream in :class:`SkillSpec`)
        and ``body`` is the Markdown body string verbatim.

    Raises:
        SkillManifestError: when the file's structure is malformed in any
            of the ways enumerated in the module docstring.
        OSError: when the file cannot be read (propagates; the scanner's
            per-skill envelope catches this).
    """
    text = path.read_text(encoding="utf-8")
    if text.startswith("﻿"):
        text = text[1:]
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise SkillManifestError(
            "missing front matter opening delimiter",
            context={"path": str(path)},
        )
    rest = text[4:]
    end_idx = rest.find("\n---\n")
    if end_idx == -1:
        # Tolerate the rare case where the file is exactly
        # ``---\n<yaml>\n---`` with no trailing newline after the closing
        # delimiter (no body).
        if rest.endswith("\n---"):
            yaml_text = rest[: -len("\n---")]
            body = ""
        else:
            raise SkillManifestError(
                "missing front matter closing delimiter",
                context={"path": str(path)},
            )
    else:
        yaml_text = rest[:end_idx]
        body = rest[end_idx + 5 :]  # skip '\n---\n'
    try:
        meta = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:
        raise SkillManifestError(
            "malformed YAML",
            context={"path": str(path), "reason": str(e)[:200]},
        ) from e
    if not isinstance(meta, dict):
        raise SkillManifestError(
            "front matter is not a YAML mapping",
            context={"path": str(path)},
        )
    return meta, body
