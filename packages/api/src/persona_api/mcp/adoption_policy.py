"""The vetted-set adoption policy for catalog-discovered self-extension (Spec N4, N4-D-6).

The single edition-aware gate that answers "which catalog apps may this persona adopt?".
v1-adoptable = a ``type: remote`` entry WITH a ``remote_url`` (N4-D-2 / N4-D-10) — the
catalog is the trust anchor for *where* it connects, so a local-container entry (no
``remote_url``) is never adoptable, and a remote entry the operator hasn't vetted is not
adoptable in cloud.

- **community** — any such remote entry (the user owns the trust choice);
- **cloud** — only the operator allowlist (``PERSONA_MCP_ADOPT_VETTED``); the **empty
  default is deny-all (fail-closed)** — nothing is catalog-adoptable until vetted.

This gate scopes **ONLY** catalog-discovered adoption (``mcp_search`` → adopt). It does NOT
touch the existing built-in / Spec-27 / N3 grant path (``grant_tool_consent``, N4-D-5):
those servers are operator-vetted by virtue of shipping, and never consult this module.

Used at both N4-D-6 boundaries: the **grant boundary** (the adopt route calls
:func:`is_adoptable` before ``create_server``) and the **search boundary**
(:func:`vetted_catalog_for_search` filters what ``mcp_search`` surfaces).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.tools.mcp.catalog import MCPCatalog

from persona_api.config import Edition

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["adoptable_catalog_names", "is_adoptable", "vetted_catalog_for_search"]


def adoptable_catalog_names(
    *, edition: Edition, vetted: Iterable[str], catalog: MCPCatalog
) -> frozenset[str]:
    """The catalog entry names a persona may ADOPT in this edition (N4-D-6).

    Args:
        edition: The running edition (community = open; cloud = operator-allowlisted).
        vetted: The operator allowlist (``PERSONA_MCP_ADOPT_VETTED``); honored only in cloud.
        catalog: The catalog to evaluate (the mirror, or a test catalog).

    Returns:
        The adoptable names. v1-adoptable requires ``server_type == "remote"`` AND a
        non-empty ``remote_url`` (N4-D-2 / N4-D-10). Community returns all such; cloud
        returns only those also in ``vetted`` (empty allowlist → empty set, fail-closed).
    """
    remote = frozenset(
        name
        for name, entry in catalog.servers.items()
        if entry.server_type == "remote" and entry.remote_url
    )
    if edition is Edition.community:
        return remote
    return remote & frozenset(vetted)


def is_adoptable(
    name: str, *, edition: Edition, vetted: Iterable[str], catalog: MCPCatalog
) -> bool:
    """Whether ``name`` is adoptable in this edition (the grant-boundary check, N4-D-6)."""
    return name in adoptable_catalog_names(edition=edition, vetted=vetted, catalog=catalog)


def vetted_catalog_for_search(
    *, edition: Edition, vetted: Iterable[str], catalog: MCPCatalog
) -> MCPCatalog:
    """The catalog ``mcp_search`` may surface — the adoptable subset (N4-D-6 search mirror).

    Mirrors the grant gate at discovery time so the persona never proposes an app it cannot
    adopt: cloud surfaces only the vetted-remote subset (empty → nothing), community surfaces
    every remote-with-url entry.
    """
    names = adoptable_catalog_names(edition=edition, vetted=vetted, catalog=catalog)
    return MCPCatalog(servers={n: catalog.servers[n] for n in names})
