"""Tool + skill catalog (spec 08, T13, §5.4).

Read-only platform-global lists of the available tools and bundled skills, for
the web app's authoring-flow checkboxes. Not RLS-scoped (no tenant data).

Tools: every built-in tool factory the runtime wires up (web_search, web_fetch,
file_read, file_write, code_execution, generate_image). Tools whose factories
require runtime context (sandbox pool, image backend) are surfaced as static
(name, description) pairs that mirror the factory module's canonical
``_DEFAULT_DESCRIPTION``; the runtime fails loud (D-12-5, D-15-X) if the
persona declares one that is not configured on the deployment.

Skills: the bundled v0.1 skill set scanned from ``persona/skills/builtin``
(architecture §9.3 + spec 13).
"""

from __future__ import annotations

from pathlib import Path

from persona.imagegen.tool import _DEFAULT_DESCRIPTION as _GENERATE_IMAGE_DESCRIPTION
from persona.sandbox.tool import _DEFAULT_DESCRIPTION as _CODE_EXECUTION_DESCRIPTION
from persona.skills import BUILTIN_ROOT, SkillScanner
from persona.tools.builtin.file_read import make_file_read_tool
from persona.tools.builtin.file_write import make_file_write_tool
from persona.tools.builtin.web_fetch import make_web_fetch_tool
from persona.tools.builtin.web_search import make_web_search_tool

__all__ = ["list_skills", "list_tools"]

# Every bundled skill folder under persona/skills/builtin (architecture §9.3,
# spec 13). The scanner emits one entry per declared skill that exists on disk.
_BUILTIN_SKILLS = [
    "data_analysis",
    "document_drafting",
    "docx_generation",
    "pdf_generation",
    "pptx_generation",
    "web_research",
    "xlsx_generation",
]


def list_tools() -> list[tuple[str, str]]:
    """The built-in tools as (name, description) pairs."""
    factory_tools = [
        make_web_search_tool(provider_name="brave", api_key=None),
        make_web_fetch_tool(),
        make_file_read_tool(sandbox_root=Path(".persona_work")),
        make_file_write_tool(sandbox_root=Path(".persona_work")),
    ]
    pairs: list[tuple[str, str]] = [(t.name, t.description) for t in factory_tools]
    # Runtime-context tools — surfaced statically because their factories
    # require a sandbox pool / image backend the catalog does not own.
    pairs.append(("code_execution", _CODE_EXECUTION_DESCRIPTION))
    pairs.append(("generate_image", _GENERATE_IMAGE_DESCRIPTION))
    return pairs


def list_skills() -> list[tuple[str, str]]:
    """The bundled skills as (name, description) pairs."""
    scanner = SkillScanner(skill_paths=[BUILTIN_ROOT])
    scanned = scanner.scan(declared_skills=_BUILTIN_SKILLS)
    return [(s.name, s.description) for s in scanned]
