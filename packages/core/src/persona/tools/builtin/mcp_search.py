"""``mcp_search`` built-in tool — discover apps in the mirrored MCP catalog (Spec N4).

A persona that hits an **in-role task it has no tool for** should not dead-end. This
tool lets it search the mirrored catalog (``github.com/docker/mcp-registry``, loaded
zero-network via :func:`persona.tools.mcp.mirror.load_mirror_catalog`) by capability or
keyword and get back candidate apps: friendly name, what the app does, and **what it
requires** — requirement *names only*.

Credential-isolation invariant (N4-D-3 / acceptance criterion 3): this tool returns
**no secret values, ever**. The catalog model (:class:`MCPSecretField`) carries no value
field by construction, and the result shape surfaces only requirement *names* (the env
var + the gateway secret key) — never an example placeholder, never a value. The
how-to-obtain guidance is the out-of-band setup form's job (Group D), not the persona's.

Ranking is a deterministic, no-dependency keyword/substring floor (T1) over the catalog's
existing text fields; results are hard-capped + a ``truncated`` flag is set so the surface
stays within the prompt budget. A semantic re-rank tier (T2, the ``bge-small`` embedder)
is a clean future seam, not wired here.

Failures surface as ``ToolResult(is_error=True, ...)`` per the ``@tool`` no-raise contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools.mcp.mirror import load_mirror_catalog
from persona.tools.protocol import AsyncTool, tool

if TYPE_CHECKING:
    from persona.tools.mcp.catalog import MCPCatalog, MCPServerCatalogEntry

__all__ = ["make_mcp_search_tool"]

_logger = get_logger("tools.mcp_search")

#: Hard cap on returned candidates — the mirror holds ~300 apps; an unbounded
#: dump would blow the prompt budget. ``top_k`` is clamped into ``[1, _MAX_RESULTS]``.
_MAX_RESULTS = 10
#: Default number of candidates returned.
_DEFAULT_TOP_K = 5

#: Field weights for the keyword floor (T1). Name/title hits dominate; the
#: capability/keyword vocabulary is next; the description is the weakest signal.
_W_NAME = 5
_W_KEYWORDS = 4
_W_CAPABILITY = 3
_W_DESCRIPTION = 2


def _score(entry: MCPServerCatalogEntry, terms: list[str]) -> int:
    """Field-weighted keyword/substring score for one entry (0 = no match)."""
    name_text = f"{entry.name} {entry.display_name}".lower()
    keyword_text = " ".join(entry.keywords).lower()
    capability_text = entry.capability.lower()
    description_text = entry.description.lower()
    total = 0
    for term in terms:
        if term in name_text:
            total += _W_NAME
        if term in keyword_text:
            total += _W_KEYWORDS
        if term in capability_text:
            total += _W_CAPABILITY
        if term in description_text:
            total += _W_DESCRIPTION
    return total


def _rank(catalog: MCPCatalog, query: str, limit: int) -> list[MCPServerCatalogEntry]:
    """The matching entries, best-first (ties broken by name for determinism)."""
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []
    scored = [
        (score, entry) for entry in catalog.servers.values() if (score := _score(entry, terms)) > 0
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return [entry for _, entry in scored[:limit]]


def _result_item(entry: MCPServerCatalogEntry) -> dict[str, Any]:
    """A candidate's model-facing shape — friendly metadata + requirement NAMES only.

    Carries **no secret value** (none exist in the catalog) and not even the example
    placeholder: requirements are the env var name + the gateway secret key only.
    """
    return {
        "name": entry.name,
        "display_name": entry.display_name or entry.name,
        "description": entry.description,
        "capability": entry.capability,
        "server_type": entry.server_type,
        "risk": entry.risk,
        "signed": entry.signed,
        "source_project": entry.source_project,
        "source_commit": entry.source_commit,
        "requires": {
            "env": list(entry.required_env),
            "secrets": [{"name": s.name, "env": s.env} for s in entry.secrets],
        },
    }


def _format_results(items: list[dict[str, Any]], *, truncated: bool) -> str:
    """Human-readable summary for ``ToolResult.content`` (no secret values)."""
    if not items:
        return "No matching apps found in the catalog."
    lines = []
    for i, item in enumerate(items, start=1):
        lines.append(f"{i}. {item['display_name']} ({item['name']}) — {item['description']}")
        reqs = item["requires"]["env"] + [s["env"] for s in item["requires"]["secrets"]]
        if reqs:
            lines.append(f"   requires setup: {', '.join(sorted(set(reqs)))}")
    if truncated:
        lines.append("(more matches exist; refine the query to narrow them)")
    return "\n".join(lines)


def make_mcp_search_tool(*, catalog: MCPCatalog | None = None) -> AsyncTool:
    """Build the ``mcp_search`` :class:`AsyncTool`.

    Args:
        catalog: The catalog to search. Defaults to the mirrored Docker catalog
            (:func:`load_mirror_catalog`, falling back to the bundled builtin
            catalog when no snapshot is present). Injectable for tests + for the
            edition-vetted-set wiring (N4-D-6, Group C).

    Returns:
        An :class:`AsyncTool` named ``mcp_search``. Returns candidate apps with
        requirement *names only* — never a secret value.
    """
    resolved_catalog = catalog if catalog is not None else load_mirror_catalog()

    @tool(
        name="mcp_search",
        description=(
            "Search the catalog of installable apps (integrations) for one that "
            "could do something you currently have no tool for. Use this ONLY when a "
            "task is within your role but you lack the capability — search by what you "
            "need to do (e.g. 'file a github issue', 'send a slack message'). Returns "
            "candidate apps with a friendly name, what each does, and what setup it "
            "requires. It does NOT enable anything: propose a candidate to the user in "
            "plain language and let them decide. Never returns or asks for secrets."
        ),
    )
    async def mcp_search(query: str, top_k: int = _DEFAULT_TOP_K) -> ToolResult:
        limit = max(1, min(top_k, _MAX_RESULTS))
        terms = [t for t in query.lower().split() if t]
        # Find one extra so we can honestly report whether matches were truncated.
        matches = _rank(resolved_catalog, query, limit + 1)
        truncated = len(matches) > limit
        items = [_result_item(e) for e in matches[:limit]]
        _logger.debug(
            "mcp_search",
            term_count=len(terms),
            match_count=len(items),
            truncated=truncated,
        )
        return ToolResult(
            tool_name="mcp_search",
            content=_format_results(items, truncated=truncated),
            data={
                "results": items,
                "query": query,
                "result_count": len(items),
                "truncated": truncated,
            },
        )

    return mcp_search
