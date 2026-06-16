"""Tool + skill catalog (spec 08, T13, §5.4).

Read-only platform-global lists of the available tools and bundled skills, for
the web app's authoring-flow checkboxes. Not RLS-scoped (no tenant data).

Tools: sourced from the persona-core known-tool catalog (spec 26 T08,
``persona.tools.TOOL_CATALOG``) — the single source of truth for every built-in
platform tool, including runtime-wired ones (``code_execution`` /
``generate_image`` / ``text_summarize``) whose factories need runtime context.
The runtime fails loud (D-12-5, D-15-X) if a persona declares a runtime-wired
tool that is not configured on the deployment.

Skills: the bundled v0.1 skill set scanned from ``persona/skills/builtin``
(architecture §9.3 + spec 13).
"""

from __future__ import annotations

from persona.skills import BUILTIN_ROOT, SkillScanner
from persona.tools import TOOL_CATALOG
from persona.tools.mcp.catalog import BUILTIN_MCP_CATALOG, recommender_provider_tag

__all__ = ["list_mcp_servers", "list_skills", "list_tools"]

# Every bundled skill folder under persona/skills/builtin (architecture §9.3,
# spec 13). The scanner emits one entry per declared skill that exists on disk.
# Spec 24 (D-24-1): the 5 document-format packs folded into document_generation.
_BUILTIN_SKILLS = [
    "code_review",
    "data_analysis",
    "document_generation",
    "web_research",
]


def list_tools() -> list[tuple[str, str]]:
    """The built-in tools as (name, description) pairs (spec 26 T08).

    Sourced from the persona-core known-tool catalog so the authoring surface
    always reflects the full tool set — including the spec-26 additions
    (calculator / datetime / regex_match / json_query / text_diff /
    currency_convert / text_summarize) — without a second hand-maintained list.
    """
    return [(entry.name, entry.description) for entry in TOOL_CATALOG]


def list_skills() -> list[tuple[str, str]]:
    """The bundled skills as (name, description) pairs."""
    scanner = SkillScanner(skill_paths=[BUILTIN_ROOT])
    scanned = scanner.scan(declared_skills=_BUILTIN_SKILLS)
    return [(s.name, s.description) for s in scanned]


def list_mcp_servers() -> list[tuple[str, str, str, bool, list[str]]]:
    """The built-in MCP servers as ``(name, description, provider, default, required_env)``.

    Spec 30 T11 — the management UI surfaces these alongside built-in tools +
    skills as one capability set. A persona enables a server by adding
    ``mcp:<name>`` to its ``tools`` allow-list. ``provider`` is the recommender
    tag (``mcp:builtin`` for default-enabled authored servers, else
    ``mcp:optional``). Sourced from the persona-core MCP catalog (the single
    source of truth, mirroring ``list_tools``); bring-your-own servers are NOT
    here (they live per-user, surfaced via ``GET /v1/mcp-servers``).
    """
    return [
        (name, e.description, recommender_provider_tag(e), e.default_enabled, list(e.required_env))
        for name, e in BUILTIN_MCP_CATALOG.servers.items()
    ]
