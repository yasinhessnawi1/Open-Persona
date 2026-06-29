"""Bring-your-own MCP store — CRUD, encryption, assignment, RLS isolation (spec 30 T09).

Against a real Postgres (migration 009 applied via ``migrated_engine``), exercises
the security-load-bearing properties at the Cluster-C checkpoint:

- credentials are stored ENCRYPTED (the raw column ≠ plaintext) and never
  returned (only ``has_credential``); they decrypt back for the connect path;
- the persona↔server assignment round-trips and yields the decrypted credential
  to the runtime-wiring path;
- the SSRF eager guard is wired into create (a loopback URL is refused);
- RLS isolation: user B cannot see user A's server (tested through the
  non-superuser ``persona_app`` role — a superuser bypasses RLS — mirroring
  ``test_rls_scope``; skips when ``APP_DATABASE_URL`` is unset).

CRUD/assignment assertions monkeypatch the SSRF *resolver* to avoid real DNS
(the guard's matrix + resolve-then-pin transport are unit-tested in
``packages/core/.../test_ssrf.py``); one test exercises the real guard on create.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from cryptography.fernet import Fernet
from persona.errors import MCPUrlNotAllowedError
from persona_api.config import APIConfig
from persona_api.errors import MCPServerNotFoundError
from persona_api.mcp import store as mcp_store
from persona_api.middleware.rls_context import current_user_id, make_rls_engine
from persona_api.services import persona_service
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

_PERSONA_YAML = """\
schema_version: "1.0"
identity:
  name: Astrid
  role: assistant
  background: A helper for the BYO-MCP store test.
tools:
  - web_search
"""


def _config() -> APIConfig:
    return APIConfig(mcp_credential_key=Fernet.generate_key().decode())


def _ensure_user(engine: Engine, owner: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
            {"i": owner, "e": f"{owner}@x"},
        )


def test_crud_encryption_and_assignment(
    migrated_engine: Engine,
    embedder: HashEmbedder384,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_store, "assert_url_allowed", lambda _url: None)  # skip DNS
    su_url = migrated_engine.url.render_as_string(hide_password=False)
    engine = make_rls_engine(su_url)
    config = _config()
    owner = "user_mcp_a"

    token = current_user_id.set(owner)
    try:
        _ensure_user(engine, owner)

        detail = mcp_store.create_server(
            rls_engine=engine,
            config=config,
            owner_id=owner,
            name="my-server",
            url="https://example.com/mcp",
            auth_method="bearer",
            credential="super-secret-token",  # noqa: S106
        )
        server_id = detail["id"]
        assert detail["has_credential"] is True
        assert "credential" not in detail  # never returned

        # credential is ENCRYPTED at rest (raw column ≠ plaintext, decrypts back).
        with engine.begin() as conn:
            raw = conn.execute(
                text("SELECT credentials_encrypted FROM user_mcp_servers WHERE id = :i"),
                {"i": server_id},
            ).scalar_one()
        assert raw is not None
        assert "super-secret-token" not in str(raw)
        cipher = mcp_store.cipher_from_config(config)
        assert cipher is not None
        assert cipher.decrypt(str(raw)) == "super-secret-token"

        # list / get round-trip.
        assert any(s["id"] == server_id for s in mcp_store.list_servers(rls_engine=engine))
        assert mcp_store.get_server(rls_engine=engine, server_id=server_id)["name"] == "my-server"

        # assignment round-trip + decrypted credential for the runtime path.
        persona_id = persona_service.create_persona(
            rls_engine=engine,
            embedder=embedder,
            audit_root=tmp_path / "audit",
            owner_id=owner,
            yaml_str=_PERSONA_YAML,
        )
        mcp_store.assign_to_persona(rls_engine=engine, persona_id=persona_id, server_id=server_id)
        assigned = mcp_store.list_servers_for_persona(rls_engine=engine, persona_id=persona_id)
        assert [s["id"] for s in assigned] == [server_id]
        decrypted = mcp_store.decrypted_servers_for_persona(
            rls_engine=engine, config=config, persona_id=persona_id
        )
        assert decrypted[0]["credential"] == "super-secret-token"  # decrypted for connect only

        mcp_store.unassign_from_persona(
            rls_engine=engine, persona_id=persona_id, server_id=server_id
        )
        assert mcp_store.list_servers_for_persona(rls_engine=engine, persona_id=persona_id) == []

        # delete.
        mcp_store.delete_server(rls_engine=engine, server_id=server_id)
        with pytest.raises(MCPServerNotFoundError):
            mcp_store.get_server(rls_engine=engine, server_id=server_id)
    finally:
        current_user_id.reset(token)
        engine.dispose()


def test_create_rejects_loopback_url_with_real_ssrf_guard(migrated_engine: Engine) -> None:
    # No monkeypatch — the real guard refuses a loopback target before any insert.
    su_url = migrated_engine.url.render_as_string(hide_password=False)
    engine = make_rls_engine(su_url)
    owner = "user_mcp_ssrf"
    token = current_user_id.set(owner)
    try:
        _ensure_user(engine, owner)
        with pytest.raises(MCPUrlNotAllowedError):
            mcp_store.create_server(
                rls_engine=engine,
                config=_config(),
                owner_id=owner,
                name="loopback",
                url="https://127.0.0.1/mcp",
                auth_method="none",
                credential=None,
            )
        assert mcp_store.list_servers(rls_engine=engine) == []
    finally:
        current_user_id.reset(token)
        engine.dispose()


def test_rls_isolation_blocks_cross_tenant(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User B cannot see user A's server — verified through the non-superuser role.

    A superuser bypasses RLS, so the isolation check MUST run as ``persona_app``
    (the request-path role). Seeds A's server as superuser, then queries as B and
    as A through the app engine.
    """
    # Read APP_DATABASE_URL LAZILY (not the module-level import-time capture): the
    # per-worktree DB-isolation conftest rewrites it to this worktree's isolated
    # ``persona_test_<worktree>`` AFTER module import, so the app-role engine must query
    # the SAME (migrated) DB the superuser seeds into — not the stale shared ``persona_test``.
    app_url_raw = os.environ.get("APP_DATABASE_URL")
    if not app_url_raw:
        pytest.skip("APP_DATABASE_URL (the non-superuser persona_app role) not set")
    monkeypatch.setattr(mcp_store, "assert_url_allowed", lambda _url: None)
    app_url = app_url_raw.replace("+asyncpg", "+psycopg")
    su_url = migrated_engine.url.render_as_string(hide_password=False)
    su_engine = make_rls_engine(su_url)
    app_engine = make_rls_engine(app_url, pool_size=2)
    owner_a, owner_b = "user_rls_a", "user_rls_b"
    config = _config()

    su_token = current_user_id.set(owner_a)
    try:
        _ensure_user(su_engine, owner_a)
        _ensure_user(su_engine, owner_b)
        server_id = mcp_store.create_server(
            rls_engine=su_engine,
            config=config,
            owner_id=owner_a,
            name="a-server",
            url="https://example.com/mcp",
            auth_method="none",
            credential=None,
        )["id"]
        current_user_id.reset(su_token)

        # As B (app role): A's server is invisible.
        b_token = current_user_id.set(owner_b)
        assert mcp_store.list_servers(rls_engine=app_engine) == []
        with pytest.raises(MCPServerNotFoundError):
            mcp_store.get_server(rls_engine=app_engine, server_id=server_id)
        current_user_id.reset(b_token)

        # As A (app role): A's server is visible (RLS lets the owner through).
        a_token = current_user_id.set(owner_a)
        assert any(s["id"] == server_id for s in mcp_store.list_servers(rls_engine=app_engine))
        current_user_id.reset(a_token)
    finally:
        su_engine.dispose()
        app_engine.dispose()
