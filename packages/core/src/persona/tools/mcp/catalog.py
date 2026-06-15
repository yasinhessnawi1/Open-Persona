"""Declarative built-in MCP server catalog (Spec 27, D-27-1 / D-27-10).

Loads the bundled ``catalog.toml`` — a declarative index of the MCP servers
Persona knows about (authored built-ins + bring-your-own external servers). This
is the **precursor** to the deferred federated MCP registry (architecture §9.4),
not the registry: resolution is 100% local, zero-network, exactly like the
Spec-24 skills catalog. ``tomllib`` (stdlib) parses the file — no new dependency.

The catalog is the shared vocabulary for:

- the **built-in launcher** (Spec 27 T4) — which servers may be spawned, and the
  default-enabled subset (``default_enabled`` ∩ ``kind="builtin"``);
- the **capability recommender** (Spec 27 T10) — MCP candidates ranked alongside
  built-in tools + skills, each carrying a provider tag;
- the runtime **MCP-gap detector** (Spec 27 T11) — maps a capability-gap utterance
  to a catalog server the persona's allow-list lacks (``keywords``, D-27-7).

Entries are frozen + ``extra="forbid"`` so a malformed catalog fails loud at
import (fail-fast). The catalog never validates a persona's declared ``mcp:``
names — unknown/external servers resolve from ``PERSONA_MCP_SERVERS`` (D-03-22).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "BUILTIN_MCP_CATALOG",
    "CATALOG_PATH",
    "MCPCatalog",
    "MCPServerCatalogEntry",
    "authored_server_names",
    "default_enabled_server_names",
    "known_mcp_server_names",
    "load_mcp_catalog",
    "mcp_server_entry",
    "recommender_provider_tag",
]

#: Bundled catalog file, shipped as package data alongside the MCP client/adapter.
CATALOG_PATH: Path = Path(__file__).parent / "catalog.toml"

MCPServerKind = Literal["builtin", "external"]
MCPRiskLevel = Literal["low", "medium", "high"]


class MCPServerCatalogEntry(BaseModel):
    """One MCP server's catalog metadata (frozen).

    Attributes:
        name: The server identifier — the ``mcp:<name>:`` prefix on its tools and
            the key in ``PERSONA_MCP_SERVERS`` / ``PERSONA_MCP_BUILTIN_ENABLED``.
        description: Authoring-facing summary (distinct from the model-facing tool
            descriptions the server advertises at connect time).
        kind: ``"builtin"`` — Persona authors + can launch this server (D-27-2);
            ``"external"`` — bring-your-own, operator-configured via
            ``PERSONA_MCP_SERVERS`` (Persona ships no code).
        default_enabled: Whether a default install enables this server. Applies to
            ``kind="builtin"`` only; with lazy spawning (D-27-3) "enabled" means
            "registered + available", not "running".
        risk: Coarse risk profile for operator review.
        required_env: Env vars the operator must set for the server to function
            (e.g. ``GITHUB_TOKEN`` for github). Never logged.
        keywords: Capability phrases that map a "I can't do X" utterance to this
            server (Spec 27 T11 gap-detection vocabulary, D-27-7). Lower-cased
            substrings matched against the model's output; empty disables mapping.
        capability: A first-person VERB phrase for the runtime MCP-gap consent
            line — slots into ``"…which would let me {capability}."`` (Spec 27
            T11). Distinct from ``description`` (a noun phrase tuned for authoring
            / the recommender list); a verb phrase keeps the consent question
            grammatical. Empty falls back to ``description``.
        notes: Optional operator-facing caveats (e.g. the fetch SSRF rationale).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    kind: MCPServerKind
    risk: MCPRiskLevel
    default_enabled: bool = False
    required_env: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    capability: str = ""
    notes: str = ""


@dataclass(frozen=True)
class MCPCatalog:
    """Parsed ``catalog.toml`` — MCP servers keyed by name.

    Attributes:
        servers: Mapping of server name → :class:`MCPServerCatalogEntry`, in file
            order (``dict`` preserves insertion order).
    """

    servers: dict[str, MCPServerCatalogEntry]


def load_mcp_catalog(path: Path = CATALOG_PATH) -> MCPCatalog:
    """Parse a ``catalog.toml`` into an :class:`MCPCatalog`.

    Args:
        path: The catalog file (defaults to the bundled one).

    Returns:
        The parsed catalog.

    Raises:
        pydantic.ValidationError: an entry is malformed (fail-fast at import).
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    servers: dict[str, MCPServerCatalogEntry] = {}
    for name, entry in data.get("server", {}).items():
        servers[name] = MCPServerCatalogEntry.model_validate({"name": name, **entry})
    return MCPCatalog(servers=servers)


#: The bundled catalog, loaded once. Read-only configuration; never mutated.
BUILTIN_MCP_CATALOG: MCPCatalog = load_mcp_catalog()


def mcp_server_entry(name: str) -> MCPServerCatalogEntry | None:
    """Return the catalog entry for ``name``, or ``None`` if unknown."""
    return BUILTIN_MCP_CATALOG.servers.get(name)


def known_mcp_server_names() -> frozenset[str]:
    """The set of all catalog server names (builtin + external)."""
    return frozenset(BUILTIN_MCP_CATALOG.servers)


def authored_server_names() -> frozenset[str]:
    """The set of server names Persona authors + can launch (``kind="builtin"``)."""
    return frozenset(name for name, e in BUILTIN_MCP_CATALOG.servers.items() if e.kind == "builtin")


def default_enabled_server_names() -> tuple[str, ...]:
    """The authored servers a default install enables, in catalog order (D-27-4)."""
    return tuple(
        name
        for name, e in BUILTIN_MCP_CATALOG.servers.items()
        if e.kind == "builtin" and e.default_enabled
    )


def recommender_provider_tag(entry: MCPServerCatalogEntry) -> str:
    """Provider tag for the unified recommender (Spec 27 T10, spec §2.3).

    ``"mcp:builtin"`` for an authored, default-enabled server (available without
    operator action); ``"mcp:optional"`` otherwise (opt-in built-in or BYO
    external — both require an operator step before the persona can use them).
    """
    if entry.kind == "builtin" and entry.default_enabled:
        return "mcp:builtin"
    return "mcp:optional"
