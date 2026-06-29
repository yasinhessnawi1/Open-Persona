"""Spec N4 B2-③ — the adopt-from-catalog orchestration (vetted → derive → write → assign).

Against real Postgres (migration 023 via ``migrated_engine``), proves the security spine of
catalog adoption:
- **vetted check before any write** — a cloud-unvetted / non-remote app is rejected
  (``MCPAppNotAdoptableError`` → 403) and NOTHING is written or assigned (fail-closed);
- **owner-scoped authz** — a user cannot adopt for another user's persona
  (``PersonaNotFoundError`` → 404) and NOTHING is written (no half-write);
- **url/auth derived from the catalog, secret from the caller** — url = the entry's
  ``remote_url`` (N4-D-10), auth = bearer iff the entry declares secrets; the credential is
  encrypted at rest + never returned;
- **double-adopt** — a clear conflict (``MCPAppAlreadyAdoptedError`` → 409), not a 500;
- ``catalog_source`` provenance persisted; the app ends up assigned to the persona.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from cryptography.fernet import Fernet
from persona.errors import PersonaNotFoundError
from persona.tools.mcp.catalog import MCPSecretField, MCPServerCatalogEntry
from persona_api.config import APIConfig, Edition
from persona_api.errors import MCPAppAlreadyAdoptedError, MCPAppNotAdoptableError
from persona_api.mcp import store as mcp_store
from persona_api.middleware.rls_context import current_user_id, make_rls_engine
from persona_api.services import adoption_service, catalog_service, persona_service
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

_SECRET = "adopt-route-token-do-not-leak"  # noqa: S105 — test sentinel, not a real credential

_NOTION = MCPServerCatalogEntry(
    name="notion-remote",
    description="Hosted Notion MCP.",
    kind="external",
    risk="medium",
    server_type="remote",
    remote_url="https://mcp.notion.com/mcp",
    secrets=(MCPSecretField(name="notion.token", env="NOTION_TOKEN"),),
)
_LOCAL = MCPServerCatalogEntry(
    name="github",
    description="Local GitHub MCP.",
    kind="external",
    risk="low",
    server_type="server",
)

_PERSONA_YAML = """\
schema_version: "1.0"
identity:
  name: Astrid
  role: assistant
  background: A helper for the N4 adoption-service test.
tools:
  - web_search
"""


def _config(*, edition: Edition = Edition.community, vetted: str = "") -> APIConfig:
    return APIConfig(
        mcp_credential_key=Fernet.generate_key().decode(),
        edition=edition,
        mcp_adopt_vetted=vetted,
    )


def _ensure_user(engine: Engine, owner: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
            {"i": owner, "e": f"{owner}@x"},
        )


def _make_persona(engine: Engine, embedder: HashEmbedder384, audit: Path, owner: str) -> str:
    return persona_service.create_persona(
        rls_engine=engine,
        embedder=embedder,
        audit_root=audit,
        owner_id=owner,
        yaml_str=_PERSONA_YAML,
    )


@pytest.fixture
def _patch_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real merged catalog has no remote entries in the test env (no mirror.json) — inject one.
    monkeypatch.setattr(catalog_service, "merged_mcp_catalog", lambda **_kw: [_NOTION, _LOCAL])
    monkeypatch.setattr(mcp_store, "assert_url_allowed", lambda _url: None)  # skip DNS


@pytest.mark.usefixtures("_patch_catalog")
def test_adopt_vetted_remote_app_happy_path(
    migrated_engine: Engine, embedder: HashEmbedder384, tmp_path: Path
) -> None:
    engine = make_rls_engine(migrated_engine.url.render_as_string(hide_password=False))
    config = _config()  # community → notion-remote adoptable
    owner = "user_adopt_ok"
    tok = current_user_id.set(owner)
    try:
        _ensure_user(engine, owner)
        persona_id = _make_persona(engine, embedder, tmp_path / "audit", owner)
        detail = adoption_service.adopt_catalog_app(
            rls_engine=engine,
            config=config,
            owner_id=owner,
            persona_id=persona_id,
            catalog_name="notion-remote",
            credential=_SECRET,
        )
        # derived from the catalog; provenance + redaction.
        assert detail["url"] == "https://mcp.notion.com/mcp"
        assert detail["auth_method"] == "bearer"  # entry declares a secret
        assert detail["catalog_source"] == "notion-remote"
        assert detail["has_credential"] is True
        assert _SECRET not in str(detail)
        # assigned to the persona.
        assigned = mcp_store.list_servers_for_persona(rls_engine=engine, persona_id=persona_id)
        assert [s["name"] for s in assigned] == ["notion-remote"]
        # encrypted at rest.
        with engine.begin() as conn:
            raw = conn.execute(
                text("SELECT credentials_encrypted FROM user_mcp_servers WHERE id = :i"),
                {"i": detail["id"]},
            ).scalar_one()
        assert _SECRET not in str(raw)
    finally:
        current_user_id.reset(tok)


@pytest.mark.usefixtures("_patch_catalog")
def test_cloud_unvetted_is_rejected_and_nothing_is_written(
    migrated_engine: Engine, embedder: HashEmbedder384, tmp_path: Path
) -> None:
    engine = make_rls_engine(migrated_engine.url.render_as_string(hide_password=False))
    config = _config(edition=Edition.cloud, vetted="")  # empty allowlist → deny-all
    owner = "user_adopt_unvetted"
    tok = current_user_id.set(owner)
    try:
        _ensure_user(engine, owner)
        persona_id = _make_persona(engine, embedder, tmp_path / "audit", owner)
        with pytest.raises(MCPAppNotAdoptableError):
            adoption_service.adopt_catalog_app(
                rls_engine=engine,
                config=config,
                owner_id=owner,
                persona_id=persona_id,
                catalog_name="notion-remote",
                credential=_SECRET,
            )
        # fail-closed: NOTHING written, NOTHING assigned.
        assert mcp_store.list_servers(rls_engine=engine) == []
        assert mcp_store.list_servers_for_persona(rls_engine=engine, persona_id=persona_id) == []
    finally:
        current_user_id.reset(tok)


@pytest.mark.usefixtures("_patch_catalog")
def test_local_container_app_is_not_adoptable(
    migrated_engine: Engine, embedder: HashEmbedder384, tmp_path: Path
) -> None:
    engine = make_rls_engine(migrated_engine.url.render_as_string(hide_password=False))
    config = _config()
    owner = "user_adopt_local"
    tok = current_user_id.set(owner)
    try:
        _ensure_user(engine, owner)
        persona_id = _make_persona(engine, embedder, tmp_path / "audit", owner)
        with pytest.raises(MCPAppNotAdoptableError):
            adoption_service.adopt_catalog_app(
                rls_engine=engine,
                config=config,
                owner_id=owner,
                persona_id=persona_id,
                catalog_name="github",  # type: server (local) → never adoptable
                credential=None,
            )
        assert mcp_store.list_servers(rls_engine=engine) == []
    finally:
        current_user_id.reset(tok)


@pytest.mark.usefixtures("_patch_catalog")
def test_cannot_adopt_for_another_users_persona(
    migrated_engine: Engine, embedder: HashEmbedder384, tmp_path: Path
) -> None:
    # Owner-scoping rides RLS, which a SUPERUSER bypasses — so the cross-tenant check MUST
    # run through the non-superuser ``persona_app`` role (the request-path role), exactly as
    # ``test_rls_isolation_blocks_cross_tenant``. Seed A as superuser; B adopts via app_engine.
    app_url_raw = os.environ.get("APP_DATABASE_URL")
    if not app_url_raw:
        pytest.skip("APP_DATABASE_URL (the non-superuser persona_app role) not set")
    su_engine = make_rls_engine(migrated_engine.url.render_as_string(hide_password=False))
    app_engine = make_rls_engine(app_url_raw.replace("+asyncpg", "+psycopg"), pool_size=2)
    config = _config()
    owner_a, owner_b = "user_adopt_a", "user_adopt_b"
    # A owns the persona (seeded as superuser).
    tok_a = current_user_id.set(owner_a)
    try:
        _ensure_user(su_engine, owner_a)
        _ensure_user(su_engine, owner_b)
        persona_a = _make_persona(su_engine, embedder, tmp_path / "audit_a", owner_a)
    finally:
        current_user_id.reset(tok_a)
    # B tries to adopt FOR A's persona via the RLS-enforced role → 404, nothing written.
    tok_b = current_user_id.set(owner_b)
    try:
        with pytest.raises(PersonaNotFoundError):
            adoption_service.adopt_catalog_app(
                rls_engine=app_engine,
                config=config,
                owner_id=owner_b,
                persona_id=persona_a,
                catalog_name="notion-remote",
                credential=_SECRET,
            )
        assert mcp_store.list_servers(rls_engine=app_engine) == []  # B wrote nothing
    finally:
        current_user_id.reset(tok_b)


@pytest.mark.usefixtures("_patch_catalog")
def test_double_adopt_is_a_clear_conflict(
    migrated_engine: Engine, embedder: HashEmbedder384, tmp_path: Path
) -> None:
    engine = make_rls_engine(migrated_engine.url.render_as_string(hide_password=False))
    config = _config()
    owner = "user_adopt_twice"
    tok = current_user_id.set(owner)
    try:
        _ensure_user(engine, owner)
        persona_id = _make_persona(engine, embedder, tmp_path / "audit", owner)
        adoption_service.adopt_catalog_app(
            rls_engine=engine,
            config=config,
            owner_id=owner,
            persona_id=persona_id,
            catalog_name="notion-remote",
            credential=_SECRET,
        )
        with pytest.raises(MCPAppAlreadyAdoptedError):
            adoption_service.adopt_catalog_app(
                rls_engine=engine,
                config=config,
                owner_id=owner,
                persona_id=persona_id,
                catalog_name="notion-remote",
                credential=_SECRET,
            )
    finally:
        current_user_id.reset(tok)
