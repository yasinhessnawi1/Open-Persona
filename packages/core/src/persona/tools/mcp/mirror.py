"""Docker MCP catalog-mirror loader — request-path, zero-network (Spec N1, D-N1-4).

Loads the bundled ``mirror.json`` snapshot (the offline-synced mirror of
``github.com/docker/mcp-registry``; see :mod:`persona.tools.mcp.mirror_sync`) and
exposes it as an :class:`~persona.tools.mcp.catalog.MCPCatalog`. Resolution is 100%
local — exactly like the builtin ``catalog.toml`` load (D-03-22, the §9.4 precursor)
— so request-time tool resolution never touches the network.

**Fail-soft is the load-bearing property (D-N1-4):** the snapshot is optional and may
be absent (no sync run yet), stale, or corrupt. This loader NEVER raises at boot — a
missing / unreadable / structurally-invalid snapshot falls back to the bundled builtin
catalog (``catalog.toml``). The mirror and the Gateway connection (D-N1-1) are
independent features: you can connect with no mirror, and mirror with no gateway.

This module is deliberately git-/yaml-free (the network-touching sync lives in
:mod:`persona.tools.mcp.mirror_sync`, never reachable from the request path).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from persona.logging import get_logger
from persona.tools.mcp.catalog import (
    BUILTIN_MCP_CATALOG,
    MCPCatalog,
    MCPServerCatalogEntry,
)

__all__ = ["MIRROR_PATH", "load_mirror_catalog"]

_log = get_logger("tools.mcp.mirror")

#: The bundled snapshot, written by the offline sync as package data beside
#: ``catalog.toml``. Absent until a sync has run (fail-soft → builtin catalog).
MIRROR_PATH: Path = Path(__file__).parent / "mirror.json"


def load_mirror_catalog(
    path: Path = MIRROR_PATH,
    *,
    fallback: MCPCatalog = BUILTIN_MCP_CATALOG,
) -> MCPCatalog:
    """Load the mirror snapshot, or fall back to the builtin catalog (never raises).

    The fallback chain (D-N1-4): present + valid → the mirror; absent / unreadable /
    corrupt / structurally-invalid → ``fallback`` (the bundled ``catalog.toml``). A
    single malformed snapshot must not break boot, so every failure mode is caught and
    logged, and ``fallback`` is returned.

    Args:
        path: The snapshot file (defaults to the bundled :data:`MIRROR_PATH`).
        fallback: The catalog to return when the snapshot is unusable (defaults to the
            builtin ``catalog.toml`` catalog).

    Returns:
        The parsed mirror catalog, or ``fallback``.
    """
    if not path.exists():
        _log.info("mcp mirror snapshot absent; using builtin catalog", path=str(path))
        return fallback
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        servers: dict[str, MCPServerCatalogEntry] = {}
        for item in raw["servers"]:
            entry = MCPServerCatalogEntry.model_validate(item)
            servers[entry.name] = entry
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
        _log.warning(
            "mcp mirror snapshot unreadable; falling back to builtin catalog",
            path=str(path),
            error=type(exc).__name__,
        )
        return fallback
    _log.info("mcp mirror snapshot loaded", path=str(path), server_count=len(servers))
    return MCPCatalog(servers=servers)
