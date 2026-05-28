"""SkillScanner — discovers declared skills under one or more paths (T04).

For each name in ``declared_skills``, the scanner:

1. Iterates the configured ``skill_paths`` in order; the **first** match wins
   (the caller orders user paths before built-ins to get user-overrides).
2. Reads the matched ``SKILL.md`` via
   :func:`persona.skills._frontmatter.parse_skill_markdown` (T03).
3. Constructs a :class:`persona.schema.skills.SkillSpec`, populating the
   spec-04 additive fields (``tools_required``, ``content``,
   ``content_token_count``) per D-04-1.
4. Validates ``tools_required`` against the persona's tool allow-list (if
   provided); missing tools log a WARNING but the skill is still returned.

Per D-04-4, the scan is **warn-and-skip** for any per-skill failure:

- missing on disk (under all configured paths) → WARNING log; skill omitted
- malformed front matter (:class:`SkillManifestError` from T03) → WARNING
  log; skill omitted
- ``SkillSpec`` Pydantic validation error (e.g., missing ``name`` /
  ``description`` in front matter) → WARNING log; skill omitted
- any other ``Exception`` raised during scan → WARNING log; skill omitted

``BaseException`` (``KeyboardInterrupt``, ``SystemExit``) propagates. The
scanner never raises domain errors itself.

Per D-04-5, an **absent** ``skill_paths`` entry (directory does not exist)
is silently skipped — no log. A persona with ``skills: []`` is the
common case; absent user-skill directories are the norm.

A user-skill that shadows a built-in by name triggers a WARNING log naming
both paths (per spec §S04-3 + D-04-5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from persona.errors import SkillManifestError
from persona.logging import get_logger
from persona.schema.skills import SkillSpec
from persona.skills._frontmatter import parse_skill_markdown
from persona.skills._tokens import count_tokens

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["SkillScanner"]

_logger = get_logger("skills.scanner")


class SkillScanner:
    """Reads declared skills from disk and produces ``SkillSpec`` instances.

    Per spec §5, the scanner is not the right place to enforce
    requirements; it's a discovery layer. Failures emit WARNING logs and
    omit the offending skill. The persona keeps loading with whatever is
    discoverable.

    Args:
        skill_paths: Ordered list of directories to search. First match
            wins per skill name. The caller orders user paths before
            built-in paths to get user-override semantics.
    """

    def __init__(self, skill_paths: list[Path]) -> None:
        self._skill_paths = list(skill_paths)

    def scan(
        self,
        declared_skills: list[str],
        *,
        tool_allow_list: list[str] | None = None,
    ) -> list[SkillSpec]:
        """Scan the configured paths for each declared skill name.

        Args:
            declared_skills: The persona's ``skills: [...]`` list. Order is
                preserved in the output list.
            tool_allow_list: The persona's tool allow-list. When supplied,
                each scanned skill's ``tools_required`` is checked against
                it; missing tools log a WARNING but do not fail the skill.

        Returns:
            One ``SkillSpec`` per successfully scanned skill, in the same
            order as ``declared_skills``. Missing or malformed skills are
            omitted with a WARNING log.
        """
        out: list[SkillSpec] = []
        for name in declared_skills:
            spec = self._scan_one(name, tool_allow_list)
            if spec is not None:
                out.append(spec)
        return out

    # Section: per-skill scan

    def _scan_one(
        self,
        name: str,
        tool_allow_list: list[str] | None,
    ) -> SkillSpec | None:
        """Locate and parse one skill. Returns ``None`` on any failure."""
        matches = self._locate(name)
        if not matches:
            _logger.warning(
                "declared skill not found",
                skill=name,
                paths=[str(p) for p in self._skill_paths],
            )
            return None
        if len(matches) > 1:
            # First match wins; remaining matches are shadowed. Log the
            # override so authors notice unexpected behaviour (D-04-5).
            _logger.warning(
                "user skill overrides another path",
                skill=name,
                used_path=str(matches[0]),
                shadowed_paths=[str(p) for p in matches[1:]],
            )
        skill_md = matches[0]
        try:
            meta, body = parse_skill_markdown(skill_md)
            spec = SkillSpec(
                name=meta.get("name", name),
                description=meta["description"],
                path=skill_md.parent,
                when_to_use=meta.get("when_to_use"),
                tools_required=list(meta.get("tools_required") or []),
                content=body,
                content_token_count=count_tokens(body),
            )
        except SkillManifestError as e:
            _logger.warning(
                "skill manifest invalid",
                skill=name,
                path=str(skill_md),
                reason=str(e),
            )
            return None
        except ValidationError as e:
            _logger.warning(
                "skill spec validation failed",
                skill=name,
                path=str(skill_md),
                errors=[err.get("msg", "") for err in e.errors()],
            )
            return None
        except KeyError as e:
            # meta["description"] missing — required by SkillSpec.
            _logger.warning(
                "skill front matter missing required field",
                skill=name,
                path=str(skill_md),
                field=str(e),
            )
            return None
        except Exception as e:  # noqa: BLE001 — D-04-4 broad envelope
            _logger.warning(
                "skill scan failed",
                skill=name,
                path=str(skill_md),
                exc_type=type(e).__name__,
                exc=str(e)[:200],
            )
            return None

        # tools_required validation runs AFTER successful SkillSpec
        # construction; missing tools warn but don't fail the skill.
        if tool_allow_list is not None:
            allowed = set(tool_allow_list)
            for tool in spec.tools_required:
                if tool not in allowed:
                    _logger.warning(
                        "skill requires tool not in persona allow-list",
                        skill=name,
                        tool=tool,
                        allowed=", ".join(sorted(allowed)),
                    )
        return spec

    def _locate(self, name: str) -> list[Path]:
        """Return all ``<path>/<name>/SKILL.md`` files that exist.

        Absent ``skill_paths`` entries are silently skipped per D-04-5.
        """
        hits: list[Path] = []
        for base in self._skill_paths:
            if not base.exists():
                continue
            candidate = base / name / "SKILL.md"
            if candidate.is_file():
                hits.append(candidate)
        return hits
