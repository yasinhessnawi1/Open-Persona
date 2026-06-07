"""Tool + skill catalog (spec 08, T13, §5.4).

Read-only platform-global lists of the available tools and bundled skills, for
the web app's authoring-flow checkboxes. Not RLS-scoped (no tenant data).

Tools: the built-in tool set (name + description from each tool factory). Skills:
the two bundled v0.1 skills (architecture §9.3) scanned from
``persona/skills/builtin``.
"""

from __future__ import annotations

from pathlib import Path

from persona.skills import BUILTIN_ROOT, SkillScanner
from persona.tools.builtin.file_read import make_file_read_tool
from persona.tools.builtin.file_write import make_file_write_tool
from persona.tools.builtin.web_fetch import make_web_fetch_tool
from persona.tools.builtin.web_search import make_web_search_tool

__all__ = ["list_skills", "list_tools"]

# The v0.1 bundled skills (architecture §9.3).
_BUILTIN_SKILLS = ["web_research", "document_drafting"]


def list_tools() -> list[tuple[str, str]]:
    """The built-in tools as (name, description) pairs."""
    tools = [
        make_web_search_tool(provider_name="brave", api_key=None),
        make_web_fetch_tool(),
        make_file_read_tool(sandbox_root=Path(".persona_work")),
        make_file_write_tool(sandbox_root=Path(".persona_work")),
    ]
    return [(t.name, t.description) for t in tools]


def list_skills() -> list[tuple[str, str]]:
    """The bundled skills as (name, description) pairs."""
    scanner = SkillScanner(skill_paths=[BUILTIN_ROOT])
    scanned = scanner.scan(declared_skills=_BUILTIN_SKILLS)
    return [(s.name, s.description) for s in scanned]
