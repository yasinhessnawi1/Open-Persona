"""Skill specification — definitions only.

Spec 04 ships the skill scanner, injector, and built-in skill packs. Spec 01
ships only ``SkillSpec`` so the persona schema's ``skills`` list can be
resolved at load time to known artifacts.

A skill is a directory containing a ``SKILL.md`` (the description and the
when-to-use guidance, per Anthropic's SKILL.md pattern) plus optional
supporting code/prompts/assets. The path is recorded so spec 04 can scan it.
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
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    path: Path
    when_to_use: str | None = None
