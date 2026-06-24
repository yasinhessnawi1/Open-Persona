"""Community-edition zero-infra boot (Spec 33, Cluster B keystone, half 2).

Proves the "clone → set a model key → run" promise without Postgres/Docker:
``create_app`` under ``PERSONA_EDITION=community`` boots on SQLite + Chroma, seeds
the single local owner, serves requests with NO auth wall, and the relational
store works through a real route — all in a temp dir, no external services.
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from persona_api.app import create_app
from persona_api.config import APIConfig, Edition

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from persona.stores.embedder import Embedder


@pytest.fixture
def community_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> Iterator[TestClient]:
    # No DATABASE_URL, no model key, no Clerk — pure zero-infra community boot.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    # Unit boot must not load torch/sentence-transformers: the real embedder pulls
    # torch (whose Linux CI wheel is broken), and these tests assert STORE routing
    # (Chroma vs Postgres), not embedding quality. Inject the deterministic fake so
    # create-time indexing and the readback query share one torch-free embedder.
    from persona_api.services import persona_service

    monkeypatch.setattr(persona_service, "default_embedder", lambda *_a, **_k: embedder)
    config = APIConfig(
        edition=Edition.community,
        community_db_path=tmp_path / "community.db",
        community_memory_path=tmp_path / "chroma",
        workspace_root=tmp_path / "work",
        audit_root=str(tmp_path / "audit"),
    )
    app = create_app(config)
    with TestClient(app) as client:  # runs the lifespan (SQLite schema-create + ensure_owner)
        yield client


def test_community_boots_on_sqlite_with_no_postgres(community_client: TestClient) -> None:
    # The relational store is a SQLite file; no Postgres engine, no admin engine.
    assert community_client.app.state.rls_engine is not None
    assert community_client.app.state.admin_engine is None
    assert str(community_client.app.state.rls_engine.url).startswith("sqlite")


def test_community_credits_endpoint_needs_no_auth_and_is_unlimited(
    community_client: TestClient,
) -> None:
    # No Authorization header — community has no auth wall.
    resp = community_client.get("/v1/me/credits")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance"] > 0
    assert body["low_balance"] is False


def test_community_relational_store_works_through_a_route(community_client: TestClient) -> None:
    # The personas list route runs a real SELECT against the SQLite store under
    # the seeded local owner; a fresh DB returns an empty list (not an error).
    resp = community_client.get("/v1/personas")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_community_persona_create_populates_memory_on_chroma_not_postgres(
    community_client: TestClient,
) -> None:
    """Persona-create on the community edition must populate memory via Chroma.

    Regression guard. ``persona_service`` used to hardcode ``PostgresBackend`` when
    composing the four typed stores for create/update, so on the community edition
    (SQLite relational store + Chroma vectors) the create-time memory population
    failed with ``no such table: memory_chunks`` — that table only exists on the
    Postgres relational store; community keeps typed-memory vectors in Chroma. The
    fix threads the app's edition-appropriate ``memory_backend`` (the one the
    lifespan wired at boot) into the store composition.

    This exercises the service layer directly with the engine + Chroma backend the
    app booted — the exact ``no such table: memory_chunks`` failure point — without
    coupling the guard to the persona-detail capability hydration (a separate
    rough edge that needs a model key, irrelevant to the storage bug). Pre-fix the
    ``registry.load_persona`` call below raises ``OperationalError`` against SQLite;
    post-fix the chunks land in Chroma and are queryable.
    """
    from persona_api.services import persona_service

    app_state = community_client.app.state
    # The app must expose the edition's memory backend; community = Chroma.
    backend = app_state.memory_backend
    assert backend is not None
    assert type(backend).__name__ == "ChromaBackend"

    yaml_str = (
        "schema_version: '1.0'\n"
        "identity:\n"
        "  name: Sigrid\n"
        "  role: research assistant\n"
        "  background: A research assistant built to help with literature reviews.\n"
        "self_facts:\n"
        "  - fact: I was built to help with literature reviews.\n"
    )
    persona_id = persona_service.create_persona(
        rls_engine=app_state.rls_engine,
        embedder=app_state.embedder,
        audit_root=app_state.audit_root,
        owner_id="local-owner",
        yaml_str=yaml_str,
        memory_backend=backend,  # the edition backend the route now threads
    )

    # The persona's typed memory was actually written to Chroma (not a no-op):
    # identity + the self-fact are queryable through the edition backend.
    identity_hits = backend.query(
        persona_id=persona_id, store_kind="identity", text="Sigrid", top_k=5
    )
    self_fact_hits = backend.query(
        persona_id=persona_id, store_kind="self_facts", text="literature reviews", top_k=5
    )
    assert identity_hits, "identity chunks should have been populated in Chroma"
    assert self_fact_hits, "self_facts chunks should have been populated in Chroma"


def test_community_owner_is_seeded(community_client: TestClient) -> None:
    from persona_api.db.community import build_community_metadata
    from sqlalchemy import select

    users = build_community_metadata().tables["users"]
    engine = community_client.app.state.rls_engine
    with engine.connect() as conn:
        ids = conn.execute(select(users.c.id)).scalars().all()
    assert "local-owner" in ids


def test_community_datetime_reads_back_tz_aware(community_client: TestClient) -> None:
    """Community SQLite datetime columns must read back UTC-aware.

    Regression for the 2nd-chat-turn 500: ``DateTime(timezone=True)`` returns NAIVE
    on SQLite, which trips the tz-aware validators (``ConversationMessage.created_at``)
    the moment a prior row is loaded — community-only (cloud TIMESTAMPTZ is aware).
    The community metadata wraps datetime columns so reads come back UTC-aware.
    """
    from datetime import datetime

    from persona_api.db.community import build_community_metadata
    from sqlalchemy import insert, select

    users = build_community_metadata().tables["users"]
    engine = community_client.app.state.rls_engine
    with engine.begin() as conn:
        conn.execute(
            insert(users).values(
                id="tz-probe", email="tz@probe.local", created_at=datetime.now(UTC)
            )
        )
        got = conn.execute(select(users.c.created_at).where(users.c.id == "tz-probe")).scalar_one()
    assert got.tzinfo is not None, "community SQLite created_at must read back tz-aware, not naive"
    assert got.utcoffset() == datetime.now(UTC).utcoffset(), "should be UTC"
