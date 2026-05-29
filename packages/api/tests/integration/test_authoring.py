"""LLM-assisted authoring routes — draft-return + refine (spec 10, T04/T05, D-10-2).

Drives the real app against Docker Postgres with a fake JWT verifier and a
*stubbed* tier registry returning a scripted backend (no real model call — that
is the @pytest.mark.external corpus eval, T06/T08). Asserts the contract change:
``/author`` returns an ``AuthoringDraft`` and creates NO persona row; creation
stays on ``POST /v1/personas``; ``/author/refine`` returns an updated draft and
rejects ``round >= 3``; the flat authoring credit is deducted per call (D-10-8).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from persona.backends.types import ChatResponse, TokenUsage
from persona_api.app import create_app
from persona_api.auth import AuthenticatedUser
from persona_api.config import APIConfig
from persona_api.middleware.rls_context import make_rls_engine
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

_DRAFT_RESPONSE = """\
schema_version: "1.0"
identity:
  name: Astrid
  role: Norwegian tenancy-law assistant
  background: Helps tenants understand husleieloven in plain language.
  language_default: nb
  constraints:
    - Do not fabricate information; say when you don't know.
    - Do not give binding legal advice; recommend a qualified lawyer.
self_facts:
  - fact: Specialises in the Norwegian Tenancy Act.
    confidence: 1.0
worldview:
  - claim: Most tenancy disputes are avoidable with a clear contract.
    domain: tenancy-law
    epistemic: belief
    confidence: 0.8
tools: []
skills: []
---QUESTIONS---
[{"section": "identity", "question": "Should Astrid serve tenants, landlords, or both?"}]"""


class _ScriptedBackend:
    async def chat(self, messages: list, **_kwargs: object) -> ChatResponse:  # noqa: ARG002
        return ChatResponse(
            content=_DRAFT_RESPONSE,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model="scripted",
            provider="scripted",
            latency_ms=0.0,
        )


class _StubRegistry:
    def __init__(self, backend: _ScriptedBackend) -> None:
        self._backend = backend

    def get(self, tier_name: str) -> _ScriptedBackend:  # noqa: ARG002
        return self._backend


@pytest.fixture
def client(
    migrated_engine: Engine,  # noqa: ARG001 — ensures schema + persona_app grants
    embedder: HashEmbedder384,
    tmp_path: Path,
) -> Iterator[tuple[TestClient, str]]:
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set")
    cfg = APIConfig(app_database_url=app_url, audit_root=str(tmp_path / "audit"))
    app = create_app(cfg)

    async def _fake_verify(token: str) -> AuthenticatedUser:
        return AuthenticatedUser(id=token, email=None)

    user_id = "user_t10"
    with TestClient(app) as c:
        app.state.verify_token = _fake_verify
        app.state.embedder = embedder
        app.state.tier_registry = _StubRegistry(_ScriptedBackend())
        app.state.authoring_tier = "frontier"
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
                {"i": user_id, "e": f"{user_id}@x.test"},
            )
        su.dispose()
        yield c, user_id
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        su.dispose()


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}"}


def test_author_returns_draft_envelope(client: tuple[TestClient, str]) -> None:
    c, uid = client
    resp = c.post(
        "/v1/personas/author",
        json={"description": "a Norwegian legal assistant focused on tenancy law"},
        headers=_auth(uid),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["yaml"].startswith("schema_version:")
    assert body["prompt_version"]  # acceptance #8: version in the response
    assert len(body["questions"]) == 1
    assert body["questions"][0]["section"] == "identity"
    assert body["errors"] is None


def test_author_creates_no_persona_row(client: tuple[TestClient, str]) -> None:
    c, uid = client
    c.post("/v1/personas/author", json={"description": "a tenancy assistant"}, headers=_auth(uid))
    # draft-return creates nothing — the list stays empty until an explicit save
    listed = c.get("/v1/personas", headers=_auth(uid)).json()
    assert listed == []


def test_author_then_save_creates_the_persona(client: tuple[TestClient, str]) -> None:
    c, uid = client
    draft = c.post(
        "/v1/personas/author", json={"description": "a tenancy assistant"}, headers=_auth(uid)
    ).json()
    created = c.post("/v1/personas", json={"yaml": draft["yaml"]}, headers=_auth(uid))
    assert created.status_code == 201, created.text
    assert "Astrid" in created.json()["yaml"]


def test_author_deducts_a_credit(client: tuple[TestClient, str]) -> None:
    c, uid = client
    before = c.get("/v1/me/credits", headers=_auth(uid)).json()["balance"]
    c.post("/v1/personas/author", json={"description": "a tenancy assistant"}, headers=_auth(uid))
    after = c.get("/v1/me/credits", headers=_auth(uid)).json()["balance"]
    assert after < before


def test_refine_returns_updated_draft(client: tuple[TestClient, str]) -> None:
    c, uid = client
    resp = c.post(
        "/v1/personas/author/refine",
        json={
            "current_yaml": _DRAFT_RESPONSE.split("---QUESTIONS---")[0].strip(),
            "question": "Should Astrid serve tenants, landlords, or both?",
            "answer": "Tenants.",
            "round": 0,
        },
        headers=_auth(uid),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["yaml"].startswith("schema_version:")


def test_refine_rejects_round_over_cap(client: tuple[TestClient, str]) -> None:
    c, uid = client
    resp = c.post(
        "/v1/personas/author/refine",
        json={"current_yaml": "schema_version: '1.0'", "question": "q", "answer": "a", "round": 3},
        headers=_auth(uid),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "refinement_limit_exceeded"
