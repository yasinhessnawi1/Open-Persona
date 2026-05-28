"""Skill specification — definitions only.

Spec 04 ships the skill scanner, injector, and built-in skill packs. Spec 01
shipped ``SkillSpec`` with four fields (``name``, ``description``, ``path``,
``when_to_use``); spec 04 extends it additively per D-04-1 with three new
optional fields populated by the scanner at scan time.

A skill is a directory containing a ``SKILL.md`` (YAML front matter + Markdown
body) plus optional supporting code/prompts/assets. The path is recorded so
spec 04 can scan it.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — Pydantic needs runtime access

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["SkillSpec"]


class SkillSpec(BaseModel):
    """Where to find a skill pack and how to describe it.

    Attributes:
        name: Stable identifier referenced from the persona YAML's
            ``skills`` list (e.g., ``"legal_research"``).
        description: One-line description; injected into the skill index in
            the system prompt (spec 05).
        path: Directory containing the skill pack, including ``SKILL.md``.
        when_to_use: Optional short hint about when this skill is relevant.
            Pulled from ``SKILL.md`` front-matter by spec 04's scanner.
        tools_required: Tools the skill needs. Validated by the scanner
            against the persona's tool allow-list; missing tools log a
            WARNING but do not fail the skill. Added in spec 04 (D-04-1).
        content: Full ``SKILL.md`` body (after front matter). Populated by
            the scanner. Empty string when constructed outside a scan (e.g.,
            spec-01 tests). Added in spec 04 (D-04-1).
        content_token_count: Pre-computed ``cl100k_base`` token count of
            ``content``. Read by the injector without re-tokenising. Added
            in spec 04 (D-04-1, D-04-2).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    path: Path
    when_to_use: str | None = None
    # Spec 04 additive fields (D-04-1). All optional; defaults keep spec-01
    # callers working without modification.
    tools_required: list[str] = Field(default_factory=list)
    content: str = ""
    content_token_count: int = Field(default=0, ge=0)
