"""Catalog-app self-adoption orchestration (Spec N4, B2-③).

The credential-isolated adopt flow a persona drives via ``mcp_search`` → propose → setup.
Reuses the Spec-30 store (N4-D-1) for the per-user credential; the route is a thin wrapper
over this service so the security spine is testable at the store level.

Order is security-load-bearing — every check runs BEFORE any write (fail-closed):

1. **owner-scoped authz** — the persona must be the caller's (``get_persona`` is RLS-scoped
   → ``PersonaNotFoundError`` → 404 if not), so a cross-tenant adopt writes nothing;
2. **vetted gate** (N4-D-6) — ``is_adoptable`` against the merged catalog; a non-remote /
   cloud-unvetted app → ``MCPAppNotAdoptableError`` → 403, nothing written;
3. **double-adopt** — a server already named for this app → ``MCPAppAlreadyAdoptedError`` →
   409 (a clear conflict, not a 500);
4. **derive + write** — ``url`` = the entry's ``remote_url`` and ``auth_method`` = bearer iff
   the entry declares a secret (both from the CATALOG, N4-D-10 — never the caller); the
   ``credential`` (from the caller) is encrypted by ``create_server`` and the row is tagged
   ``catalog_source``; then assigned to the persona.

The credential never reaches model context, logs, audit, or the response — it rides a
``repr=False`` request field, is encrypted at rest, and ``create_server`` returns only the
redacted detail (``has_credential``). The audit (in the route) carries name + provenance only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from persona.tools.mcp.catalog import MCPCatalog

from persona_api.config import Edition
from persona_api.errors import MCPAppAlreadyAdoptedError, MCPAppNotAdoptableError
from persona_api.mcp import store as mcp_store
from persona_api.mcp.adoption_policy import is_adoptable
from persona_api.services import catalog_service, persona_service

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from persona_api.config import APIConfig

__all__ = ["adopt_catalog_app"]


def adopt_catalog_app(
    *,
    rls_engine: Engine,
    config: APIConfig,
    owner_id: str,
    persona_id: str,
    catalog_name: str,
    credential: str | None,
) -> dict[str, Any]:
    """Adopt a catalog app for a persona (the B2-③ orchestration). Returns the redacted detail.

    Raises:
        PersonaNotFoundError: the persona is not the caller's (→ 404). Nothing written.
        MCPAppNotAdoptableError: the vetted gate refused the app (→ 403). Nothing written.
        MCPAppAlreadyAdoptedError: the caller already has a server by this app's name (→ 409).
        MCPServerValidationError: the app declares a secret but no credential was supplied
            (→ 422, from ``create_server``).
    """
    # 1. owner-scoped authz — RLS-scoped read; raises PersonaNotFoundError if not the caller's.
    persona_service.get_persona(rls_engine=rls_engine, persona_id=persona_id)

    # 2. vetted gate (N4-D-6) — BEFORE any write.
    catalog = MCPCatalog(servers={e.name: e for e in catalog_service.merged_mcp_catalog()})
    if not is_adoptable(
        catalog_name,
        edition=config.edition,
        vetted=config.mcp_adopt_vetted_list,
        catalog=catalog,
    ):
        reason = (
            "not in the operator-vetted set"
            if config.edition is Edition.cloud
            else "not a remote app available for adoption"
        )
        raise MCPAppNotAdoptableError(
            "app is not adoptable", context={"app": catalog_name, "reason": reason}
        )
    entry = catalog.servers[catalog_name]

    # 3. double-adopt — a clear conflict, never a 500.
    if any(s["name"] == catalog_name for s in mcp_store.list_servers(rls_engine=rls_engine)):
        raise MCPAppAlreadyAdoptedError("app already adopted", context={"app": catalog_name})

    # 4. derive (url/auth from the CATALOG) + write (credential encrypted) + assign.
    auth_method = "bearer" if entry.secrets else "none"
    detail = mcp_store.create_server(
        rls_engine=rls_engine,
        config=config,
        owner_id=owner_id,
        name=catalog_name,
        url=entry.remote_url,
        auth_method=auth_method,
        credential=credential,
        catalog_source=catalog_name,
    )
    mcp_store.assign_to_persona(
        rls_engine=rls_engine, persona_id=persona_id, server_id=detail["id"]
    )
    return detail
