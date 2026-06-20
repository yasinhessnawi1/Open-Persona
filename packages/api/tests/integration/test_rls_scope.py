"""Structural RLS scope: the D-08-1 mechanism, as a real integration test (T05).

Formalises the Phase-3 spike (research §1) against the live RLS-forced DB as the
non-superuser ``persona_app`` role. Mounts a route that, in ONE request, does
BOTH a direct route-style query AND a ``PostgresBackend`` store call — and
asserts each user sees only their own rows, including the runtime-store path, and
that an unauthenticated path fails closed.

This is the per-route proof's nucleus; T15 generalises it across every endpoint.
Needs Docker Postgres + the ``persona_app`` role (APP_DATABASE_URL); skips
otherwise.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from persona.schema.chunks import PersonaChunk
from persona.stores.postgres import PostgresBackend
from persona_api.auth import AuthenticatedUser, get_current_user
from persona_api.editions.owner_resolver import CloudOwnerResolver
from persona_api.errors import register_exception_handlers
from persona_api.middleware.rls_context import get_rls_connection, make_rls_engine
from sqlalchemy import Connection, create_engine, text

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

_APP_URL = os.environ.get("APP_DATABASE_URL")


def _require_app_role() -> str:
    if not _APP_URL:
        pytest.skip("APP_DATABASE_URL (the non-superuser persona_app role) not set")
    return _APP_URL.replace("+asyncpg", "+psycopg")


def _seed_two_tenants(superuser_url: str, emb: HashEmbedder384) -> tuple[str, str, str, str]:
    """As superuser (RLS bypassed), create users A,B + a persona + a memory chunk
    each. Returns (user_a, user_b, persona_a, persona_b)."""
    su = create_engine(superuser_url)
    ua, ub = f"u_a_{uuid.uuid4().hex[:8]}", f"u_b_{uuid.uuid4().hex[:8]}"
    pa, pb = f"p_a_{uuid.uuid4().hex[:8]}", f"p_b_{uuid.uuid4().hex[:8]}"
    with su.begin() as c:
        for u in (ua, ub):
            c.execute(
                text("INSERT INTO users (id, email) VALUES (:i, :e)"), {"i": u, "e": f"{u}@x.test"}
            )
        c.execute(
            text("INSERT INTO personas (id, owner_id, yaml) VALUES (:i, :o, 'y')"),
            {"i": pa, "o": ua},
        )
        c.execute(
            text("INSERT INTO personas (id, owner_id, yaml) VALUES (:i, :o, 'y')"),
            {"i": pb, "o": ub},
        )
    backend = PostgresBackend(engine=su, embedder=emb)
    backend.upsert(persona_id=pa, store_kind="self_facts", chunks=[_chunk("A secret fact")])
    backend.upsert(persona_id=pb, store_kind="self_facts", chunks=[_chunk("B secret fact")])
    su.dispose()
    return ua, ub, pa, pb


def _chunk(text_: str) -> PersonaChunk:
    from datetime import UTC, datetime

    return PersonaChunk(
        id=f"c::{uuid.uuid4()}", text=text_, metadata={}, created_at=datetime.now(UTC)
    )


def _build_app(rls_engine: Engine, persona_lookup: dict[str, str], emb: HashEmbedder384) -> FastAPI:
    """An app with ONE route that does a direct query AND a store call per request."""
    app = FastAPI()
    register_exception_handlers(app)
    app.state.rls_engine = rls_engine

    async def _fake_verify(token: str) -> AuthenticatedUser:
        return AuthenticatedUser(id=token, email=None)  # token == user_id, for the test

    app.state.verify_token = _fake_verify
    # Spec 33: get_current_user resolves the owner via the edition's
    # OwnerResolver (reads app.state.owner_resolver). The cloud resolver
    # extracts the bearer + calls the verify callable above.
    app.state.owner_resolver = CloudOwnerResolver()

    @app.get("/probe")
    async def _probe(
        user: AuthenticatedUser = Depends(get_current_user),
        conn: Connection = Depends(get_rls_connection),
    ) -> dict[str, list[str]]:
        # (1) direct route-style query — RLS-scoped by the pool listener.
        direct = conn.execute(text("SELECT owner_id FROM personas")).scalars().all()
        # (2) store call through the REAL backend (opens its OWN transaction on
        # the SAME RLS engine → also scoped by the checkout listener).
        backend = PostgresBackend(engine=rls_engine, embedder=emb)
        my_persona = persona_lookup[user.id]
        chunks = backend.get_all(persona_id=my_persona, store_kind="self_facts")
        return {"direct": list(direct), "store": [c.text for c in chunks]}

    @app.get("/probe-cross/{other_persona}")
    async def _probe_cross(
        other_persona: str,
        user: AuthenticatedUser = Depends(get_current_user),  # noqa: ARG001
    ) -> dict[str, int]:
        # As the current user, try to read ANOTHER tenant's persona store → 0 rows.
        backend = PostgresBackend(engine=rls_engine, embedder=emb)
        chunks = backend.get_all(persona_id=other_persona, store_kind="self_facts")
        return {"rows": len(chunks)}

    return app


def test_structural_rls_scopes_route_and_store_per_tenant(
    migrated_engine: Engine, database_url: str, embedder: HashEmbedder384
) -> None:
    # `migrated_engine` ensures the schema + RLS policies exist and persona_app
    # is granted, regardless of test order (other migration tests drop the
    # schema). It's the spec-07 harness; we don't use the engine directly (we
    # build our own RLS engine), just its setup side-effect.
    _ = migrated_engine
    app_url = _require_app_role()
    ua, ub, pa, pb = _seed_two_tenants(database_url, embedder)
    lookup = {ua: pa, ub: pb}

    rls_engine = make_rls_engine(app_url, pool_size=2)
    try:
        client = TestClient(_build_app(rls_engine, lookup, embedder))

        # As A: both the direct query AND the store call see ONLY A's rows.
        resp = client.get("/probe", headers={"Authorization": f"Bearer {ua}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["direct"] == [ua]
        assert body["store"] == ["A secret fact"]

        # As B: only B's rows.
        resp = client.get("/probe", headers={"Authorization": f"Bearer {ub}"})
        body = resp.json()
        assert body["direct"] == [ub]
        assert body["store"] == ["B secret fact"]

        # As A, try to read B's persona store → 0 rows (cross-tenant, fail-closed).
        resp = client.get(f"/probe-cross/{pb}", headers={"Authorization": f"Bearer {ua}"})
        assert resp.json() == {"rows": 0}
    finally:
        rls_engine.dispose()
        _cleanup(database_url, ua, ub)


def test_unauthenticated_request_never_reaches_db(
    migrated_engine: Engine, embedder: HashEmbedder384
) -> None:
    _ = migrated_engine
    app_url = _require_app_role()
    rls_engine = make_rls_engine(app_url, pool_size=2)
    try:
        client = TestClient(_build_app(rls_engine, {}, embedder))
        # No Authorization header → 401 before the DB dependency runs (fail-closed).
        resp = client.get("/probe")
        assert resp.status_code == 401
    finally:
        rls_engine.dispose()


def _cleanup(superuser_url: str, *user_ids: str) -> None:
    su = create_engine(superuser_url)
    with su.begin() as c:
        for u in user_ids:
            c.execute(text("DELETE FROM users WHERE id = :i"), {"i": u})  # cascades
    su.dispose()
