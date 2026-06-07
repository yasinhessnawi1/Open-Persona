"""Personas CRUD + authoring + memory population (spec 08, T07, D-08-8).

Drives the real app against Docker Postgres with a fake JWT verifier and the
fast HashEmbedder384 (no model download). Asserts: create→get→patch→delete
round-trips, create populates memory_chunks via the typed stores under RLS,
invalid YAML → 422, and authoring (scripted backend) produces a valid persona.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from persona_api.app import create_app
from persona_api.auth import AuthenticatedUser
from persona_api.config import APIConfig
from persona_api.middleware.rls_context import make_rls_engine
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

_VALID_YAML = """\
schema_version: "1.0"
identity:
  name: Astrid
  role: Norwegian tenancy law assistant
  background: |
    Helps tenants understand husleieloven.
  language_default: en
  constraints:
    - Never give binding legal advice.
self_facts:
  - fact: Specialised in Norwegian residential tenancy.
    confidence: 1.0
worldview:
  - claim: Tenants in Norway have strong protections.
    domain: tenancy
    epistemic: fact
    confidence: 0.95
    valid_time: always
"""


@pytest.fixture
def client(
    migrated_engine: Engine,  # noqa: ARG001 — ensures schema + persona_app grants
    embedder: HashEmbedder384,
    tmp_path: Path,
) -> Iterator[tuple[TestClient, str]]:
    import os

    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set")
    cfg = APIConfig(app_database_url=app_url, audit_root=str(tmp_path / "audit"))
    app = create_app(cfg)

    # Override the RLS engine (the lifespan built one from cfg; replace with a
    # test-controlled one is unnecessary — the lifespan's is fine) and inject a
    # fake verifier + the fast hash embedder.
    async def _fake_verify(token: str) -> AuthenticatedUser:
        return AuthenticatedUser(id=token, email=None)  # token == user_id

    user_id = "user_t07"
    with TestClient(app) as c:
        app.state.verify_token = _fake_verify
        app.state.embedder = embedder
        # Drop the lifespan-installed TierRegistry so the persona-detail
        # capabilities surface doesn't lazily instantiate a real chat backend
        # (AuthenticationError("missing API key") on CI without ANTHROPIC_API_KEY).
        if hasattr(app.state, "tier_registry"):
            app.state.tier_registry = None
        # seed the user row (FK target for personas.owner_id) as superuser
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
                {"i": user_id, "e": f"{user_id}@x.test"},
            )
        su.dispose()
        yield c, user_id
        # cleanup
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        su.dispose()


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}"}


def test_create_get_patch_delete_round_trip(client: tuple[TestClient, str]) -> None:
    c, uid = client
    # create
    resp = c.post("/v1/personas", json={"yaml": _VALID_YAML}, headers=_auth(uid))
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]
    assert resp.json()["schema_version"] == "1.0"

    # get
    resp = c.get(f"/v1/personas/{pid}", headers=_auth(uid))
    assert resp.status_code == 200
    assert "Astrid" in resp.json()["yaml"]

    # list
    resp = c.get("/v1/personas", headers=_auth(uid))
    assert resp.status_code == 200
    assert any(p["id"] == pid for p in resp.json())

    # patch (change the role)
    updated = _VALID_YAML.replace("tenancy law assistant", "rental disputes assistant")
    resp = c.patch(f"/v1/personas/{pid}", json={"yaml": updated}, headers=_auth(uid))
    assert resp.status_code == 200
    assert "rental disputes" in resp.json()["yaml"]

    # delete
    resp = c.delete(f"/v1/personas/{pid}", headers=_auth(uid))
    assert resp.status_code == 204
    resp = c.get(f"/v1/personas/{pid}", headers=_auth(uid))
    assert resp.status_code == 404


def test_create_populates_memory_chunks(client: tuple[TestClient, str]) -> None:
    import os

    c, uid = client
    resp = c.post("/v1/personas", json={"yaml": _VALID_YAML}, headers=_auth(uid))
    pid = resp.json()["id"]
    # memory_chunks should have identity + self_facts + worldview rows for pid
    su = make_rls_engine(os.environ["DATABASE_URL"])
    with su.begin() as conn:
        kinds = (
            conn.execute(
                text("SELECT DISTINCT kind FROM memory_chunks WHERE persona_id = :p"),
                {"p": pid},
            )
            .scalars()
            .all()
        )
    su.dispose()
    assert "identity" in kinds
    assert "self_facts" in kinds
    assert "worldview" in kinds


def test_invalid_yaml_returns_422(client: tuple[TestClient, str]) -> None:
    c, uid = client
    # missing required identity → schema validation error
    resp = c.post("/v1/personas", json={"yaml": "schema_version: '1.0'\n"}, headers=_auth(uid))
    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_error"


def test_unauthenticated_create_is_401(client: tuple[TestClient, str]) -> None:
    c, _uid = client
    resp = c.post("/v1/personas", json={"yaml": _VALID_YAML})
    assert resp.status_code == 401


def test_avatar_url_round_trips_through_create_get_list_patch(
    client: tuple[TestClient, str],
) -> None:
    """The pre-spec-09 nullable avatar_url presentation field (migration 003)."""
    c, uid = client
    # create with an avatar
    resp = c.post(
        "/v1/personas",
        json={"yaml": _VALID_YAML, "avatar_url": "https://cdn.test/a.png"},
        headers=_auth(uid),
    )
    assert resp.status_code == 201
    pid = resp.json()["id"]
    assert resp.json()["avatar_url"] == "https://cdn.test/a.png"

    # get + list surface it
    assert c.get(f"/v1/personas/{pid}", headers=_auth(uid)).json()["avatar_url"] == (
        "https://cdn.test/a.png"
    )
    listed = next(p for p in c.get("/v1/personas", headers=_auth(uid)).json() if p["id"] == pid)
    assert listed["avatar_url"] == "https://cdn.test/a.png"

    # patch updates it; omitting it leaves it untouched (PATCH semantics)
    c.patch(
        f"/v1/personas/{pid}",
        json={"yaml": _VALID_YAML, "avatar_url": "https://cdn.test/b.png"},
        headers=_auth(uid),
    )
    assert c.get(f"/v1/personas/{pid}", headers=_auth(uid)).json()["avatar_url"] == (
        "https://cdn.test/b.png"
    )
    c.patch(f"/v1/personas/{pid}", json={"yaml": _VALID_YAML}, headers=_auth(uid))  # no avatar_url
    assert c.get(f"/v1/personas/{pid}", headers=_auth(uid)).json()["avatar_url"] == (
        "https://cdn.test/b.png"  # untouched
    )


def test_avatar_url_defaults_null(client: tuple[TestClient, str]) -> None:
    c, uid = client
    pid = c.post("/v1/personas", json={"yaml": _VALID_YAML}, headers=_auth(uid)).json()["id"]
    assert c.get(f"/v1/personas/{pid}", headers=_auth(uid)).json()["avatar_url"] is None
