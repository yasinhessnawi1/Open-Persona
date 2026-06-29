"""Bring-your-own MCP server store + lifecycle service (spec 30 T09, D-30-3/4/5/6).

CRUD + test-connection/discovery + persona assignment for user-owned MCP
servers. RLS-scoped (the request's ``app.current_user_id`` is bound by the auth
dependency; every query runs through the ``rls_engine`` so a user only ever
touches their own rows). The two security-load-bearing properties:

- **SSRF** — the user-supplied URL is validated with
  :func:`persona.tools.mcp.ssrf.assert_url_allowed` at create/update/test
  (eager) and resolve-then-pinned on every live connect (:mod:`...ssrf`). https
  only; private/loopback/metadata targets refused.
- **Credentials** — encrypted at rest (T07, :mod:`persona_api.mcp.crypto`),
  never returned to the client (only ``has_credential``) and never logged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from persona.errors import MCPServerUnavailableError, MCPUrlNotAllowedError
from persona.logging import get_logger
from persona.tools.mcp.client import MCPClient
from persona.tools.mcp.ssrf import assert_url_allowed
from sqlalchemy import delete, insert, select, update

from persona_api.db.models import persona_mcp_assignments as assignments_t
from persona_api.db.models import personas as personas_t
from persona_api.db.models import user_mcp_servers as servers_t
from persona_api.errors import (
    MCPCredentialError,
    MCPServerNotFoundError,
    MCPServerValidationError,
)
from persona_api.mcp.crypto import cipher_from_config

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from persona_api.config import APIConfig

__all__ = [
    "assign_to_persona",
    "create_server",
    "decrypted_servers_for_persona",
    "delete_server",
    "get_server",
    "list_servers",
    "list_servers_for_persona",
    "test_connection",
    "unassign_from_persona",
    "update_server",
]

_log = get_logger("api.mcp.store")


def _to_detail(row: dict[str, Any]) -> dict[str, Any]:
    """Project a DB row to the MCPServerDetail shape (credential REDACTED)."""
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "url": str(row["url"]),
        "auth_method": str(row["auth_method"]),
        "enabled": bool(row["enabled"]),
        "has_credential": row["credentials_encrypted"] is not None,
        "discovered_tools": row["discovered_tools"],
        # Adoption provenance (Spec N4, N4-D-9): the catalog entry an adoption came from,
        # or None for a manually-added BYO server. NOT a secret — display metadata only.
        "catalog_source": row.get("catalog_source"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _encrypt_credential(config: APIConfig, auth_method: str, credential: str | None) -> str | None:
    """Encrypt a credential for storage, or return None for no-auth servers.

    Raises:
        MCPServerValidationError: auth requested but no credential supplied.
        MCPCredentialError: a credential was supplied but no key is configured
            (never store a secret in plaintext — fail loud, D-30-4).
    """
    if auth_method == "none":
        return None
    if not credential:
        raise MCPServerValidationError(
            "auth_method requires a credential", context={"reason": "missing_credential"}
        )
    cipher = cipher_from_config(config)
    if cipher is None:
        raise MCPCredentialError(
            "credential encryption is not configured (set MCP_CREDENTIAL_KEY)",
            context={"reason": "no_key"},
        )
    return cipher.encrypt(credential)


def _auth_headers_for_row(config: APIConfig, row: dict[str, Any]) -> dict[str, str] | None:
    """Build the outbound auth header from a row's stored credential, or None.

    Decrypts transiently (in memory) only to authenticate the connect — the
    plaintext is never returned over the API or logged. ``bearer`` →
    ``Authorization: Bearer <token>``. No-auth / no-key → no header.
    """
    if row["auth_method"] == "bearer" and row["credentials_encrypted"] is not None:
        cipher = cipher_from_config(config)
        if cipher is None:
            return None
        token = cipher.decrypt(str(row["credentials_encrypted"]))
        return {"Authorization": f"Bearer {token}"}
    return None


def create_server(
    *,
    rls_engine: Engine,
    config: APIConfig,
    owner_id: str,
    name: str,
    url: str,
    auth_method: str,
    credential: str | None,
    catalog_source: str | None = None,
) -> dict[str, Any]:
    """Create a BYO MCP server (SSRF-validated, credential encrypted). Returns the detail.

    ``catalog_source`` (Spec N4, N4-D-9) records the catalog entry a self-extension
    adoption came from; ``None`` (the default) marks a manually-added BYO server, keeping
    the pre-N4 call sites byte-identical. It is provenance metadata — never a secret.
    """
    assert_url_allowed(url)  # eager SSRF gate (https + public target)
    encrypted = _encrypt_credential(config, auth_method, credential)
    with rls_engine.begin() as conn:
        row = (
            conn.execute(
                insert(servers_t)
                .values(
                    owner_id=owner_id,
                    name=name,
                    url=url,
                    auth_method=auth_method,
                    credentials_encrypted=encrypted,
                    catalog_source=catalog_source,
                )
                .returning(*servers_t.c)
            )
            .mappings()
            .first()
        )
    if row is None:  # pragma: no cover — RLS WITH CHECK would reject, not return None
        raise MCPServerValidationError("could not create server", context={"reason": "rls_reject"})
    return _to_detail(dict(row))


def list_servers(*, rls_engine: Engine) -> list[dict[str, Any]]:
    """List the caller's BYO MCP servers (RLS-scoped)."""
    with rls_engine.begin() as conn:
        rows = (
            conn.execute(select(servers_t).order_by(servers_t.c.created_at.desc())).mappings().all()
        )
    return [_to_detail(dict(r)) for r in rows]


def get_server(*, rls_engine: Engine, server_id: str) -> dict[str, Any]:
    """Return one server (RLS-scoped → 404 when not the caller's)."""
    return _to_detail(_require_row(rls_engine, server_id))


def _require_row(rls_engine: Engine, server_id: str) -> dict[str, Any]:
    with rls_engine.begin() as conn:
        row = conn.execute(select(servers_t).where(servers_t.c.id == server_id)).mappings().first()
    if row is None:
        raise MCPServerNotFoundError("mcp server not found", context={"id": server_id})
    return dict(row)


def update_server(
    *,
    rls_engine: Engine,
    config: APIConfig,
    server_id: str,
    name: str | None = None,
    url: str | None = None,
    auth_method: str | None = None,
    credential: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Patch a server (omitted fields unchanged). Re-validates a new URL; re-encrypts creds."""
    current = _require_row(rls_engine, server_id)
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if url is not None:
        assert_url_allowed(url)
        values["url"] = url
    if enabled is not None:
        values["enabled"] = enabled
    # Credential / auth changes: recompute the encrypted blob against the
    # effective auth_method (the incoming one, else the stored one).
    if auth_method is not None or credential is not None:
        effective_auth = auth_method if auth_method is not None else str(current["auth_method"])
        if auth_method is not None:
            values["auth_method"] = effective_auth
        if effective_auth == "none":
            values["credentials_encrypted"] = None
        elif credential is not None:
            values["credentials_encrypted"] = _encrypt_credential(
                config, effective_auth, credential
            )
    if not values:
        return _to_detail(current)
    from datetime import UTC, datetime

    values["updated_at"] = datetime.now(UTC)
    with rls_engine.begin() as conn:
        row = (
            conn.execute(
                update(servers_t)
                .where(servers_t.c.id == server_id)
                .values(**values)
                .returning(*servers_t.c)
            )
            .mappings()
            .first()
        )
    if row is None:  # pragma: no cover
        raise MCPServerNotFoundError("mcp server not found", context={"id": server_id})
    return _to_detail(dict(row))


def delete_server(*, rls_engine: Engine, server_id: str) -> None:
    """Delete a server (RLS-scoped → 404 when not the caller's). Assignments cascade."""
    with rls_engine.begin() as conn:
        result = conn.execute(
            delete(servers_t).where(servers_t.c.id == server_id).returning(servers_t.c.id)
        )
        if result.first() is None:
            raise MCPServerNotFoundError("mcp server not found", context={"id": server_id})


async def test_connection(
    *, rls_engine: Engine, config: APIConfig, server_id: str
) -> dict[str, Any]:
    """Connect to the server (SSRF-pinned) + discover tools; cache them (D-30-5).

    Returns ``{ok, tools, error}``. A connection failure is reported, not raised
    (the row is unchanged); an SSRF rejection IS surfaced as ``ok=false`` with a
    category. Decrypts the credential transiently only to authenticate — it is
    never returned or logged.
    """
    row = _require_row(rls_engine, server_id)
    try:
        assert_url_allowed(str(row["url"]))  # re-validate before connecting
        client = MCPClient(
            server_name=str(row["name"]),
            server_url=str(row["url"]),
            enforce_ssrf=True,  # the LIVE pinned path — resolve-then-pin per request
            headers=_auth_headers_for_row(config, row),
        )
        await client.connect(strict=True)
        tools = [t.name for t in client.get_tools()]
        await client.disconnect(reason="test_connection")
    except MCPUrlNotAllowedError as exc:
        return {"ok": False, "tools": [], "error": exc.context.get("reason", "blocked")}
    except MCPServerUnavailableError:
        return {"ok": False, "tools": [], "error": "unreachable"}
    # Cache the discovered tools on the row (lazy refresh on later use).
    from datetime import UTC, datetime

    with rls_engine.begin() as conn:
        conn.execute(
            update(servers_t)
            .where(servers_t.c.id == server_id)
            .values(discovered_tools=tools, updated_at=datetime.now(UTC))
        )
    return {"ok": True, "tools": tools, "error": None}


def assign_to_persona(*, rls_engine: Engine, persona_id: str, server_id: str) -> None:
    """Assign a server to a persona (D-30-6). Idempotent. RLS guards both ends.

    Both the persona and the server must be the caller's (RLS hides others); the
    join row's RLS policy scopes through the persona's owner. A non-owned persona
    or server is invisible → 404.
    """
    _require_row(rls_engine, server_id)  # 404 if not the caller's server
    with rls_engine.begin() as conn:
        persona = conn.execute(select(personas_t.c.id).where(personas_t.c.id == persona_id)).first()
        if persona is None:
            raise MCPServerNotFoundError("persona not found", context={"id": persona_id})
        existing = conn.execute(
            select(assignments_t.c.server_id).where(
                assignments_t.c.persona_id == persona_id,
                assignments_t.c.server_id == server_id,
            )
        ).first()
        if existing is None:
            conn.execute(insert(assignments_t).values(persona_id=persona_id, server_id=server_id))


def unassign_from_persona(*, rls_engine: Engine, persona_id: str, server_id: str) -> None:
    """Remove a persona↔server assignment (idempotent; RLS-scoped)."""
    with rls_engine.begin() as conn:
        conn.execute(
            delete(assignments_t).where(
                assignments_t.c.persona_id == persona_id,
                assignments_t.c.server_id == server_id,
            )
        )


def list_servers_for_persona(*, rls_engine: Engine, persona_id: str) -> list[dict[str, Any]]:
    """List the servers assigned to a persona (RLS-scoped; credential redacted)."""
    with rls_engine.begin() as conn:
        rows = (
            conn.execute(
                select(servers_t)
                .join(assignments_t, assignments_t.c.server_id == servers_t.c.id)
                .where(assignments_t.c.persona_id == persona_id)
                .order_by(servers_t.c.name.asc())
            )
            .mappings()
            .all()
        )
    return [_to_detail(dict(r)) for r in rows]


def decrypted_servers_for_persona(
    *, rls_engine: Engine, config: APIConfig, persona_id: str
) -> list[dict[str, Any]]:
    """Internal: enabled assigned servers with the credential DECRYPTED for connect (T10).

    Used only by the runtime-factory wiring to build :class:`MCPClient`s. The
    plaintext credential lives in memory for the connect and is never persisted,
    logged, or returned over the API. Disabled servers are omitted.
    """
    cipher = cipher_from_config(config)
    with rls_engine.begin() as conn:
        rows = (
            conn.execute(
                select(servers_t)
                .join(assignments_t, assignments_t.c.server_id == servers_t.c.id)
                .where(assignments_t.c.persona_id == persona_id, servers_t.c.enabled.is_(True))
            )
            .mappings()
            .all()
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        credential: str | None = None
        if r["credentials_encrypted"] is not None:
            if cipher is None:
                # A stored credential but no key — skip the server (cannot auth);
                # fail closed rather than connect unauthenticated.
                _log.warning(
                    "mcp server {name} has a credential but no key; skipped", name=r["name"]
                )
                continue
            credential = cipher.decrypt(str(r["credentials_encrypted"]))
        out.append(
            {
                "id": str(r["id"]),
                "name": str(r["name"]),
                "url": str(r["url"]),
                "auth_method": str(r["auth_method"]),
                "credential": credential,
            }
        )
    return out
