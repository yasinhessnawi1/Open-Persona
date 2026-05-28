"""Skill index renderer — pure function (T05, D-04-6).

Produces the compact "available skills" Markdown block that spec 05's
prompt builder injects into every system prompt. The model reads this to
decide whether a skill applies; the full ``SKILL.md`` content is injected
only on activation (spec 05's runtime invokes
:class:`persona.skills.injector.SkillInjector` after a successful
``use_skill`` tool call).

Per D-04-6, this is a **pure function**:
- Input: ``list[SkillSpec]``.
- Output: ``str`` (Markdown).
- No I/O, no logging, no clock, no module state. Same input → same output.

An empty input list returns an empty string — the prompt builder shouldn't
inject a "Available skills:" header with no skills under it.

A skill with ``when_to_use=None`` renders only its ``name`` + ``description``
line; the "Use when:" sub-line is omitted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from persona.schema.skills import SkillSpec

__all__ = ["render_skill_index"]

_HEADER = "Available skills (use when appropriate):"


def render_skill_index(skills: list[SkillSpec]) -> str:
    """Render the compact skill index Markdown block.

    Args:
        skills: The persona's scanned skills, in the order they should be
            presented to the model. Caller controls ordering (usually the
            declared ``skills: [...]`` order from the persona YAML).

    Returns:
        Markdown block as in spec §6, or empty string if ``skills`` is
        empty. No trailing whitespace; lines joined with ``\\n``.
    """
    if not skills:
        return ""
    lines = [_HEADER, ""]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
        if skill.when_to_use:
            lines.append(f"  Use when: {skill.when_to_use}")
    return "\n".join(lines)
