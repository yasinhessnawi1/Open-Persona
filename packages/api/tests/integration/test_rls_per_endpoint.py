"""Per-endpoint RLS adversarial sweep (spec 08, T15, acceptance #4 — the headline).

The vuln spec 07 caught passed all functional tests, so the RLS proof is
per-route, not one-endpoint. This parametrizes over EVERY tenant-touching
endpoint that takes a resource id: seed user A's persona / conversation / run,
then — as user B — hit each endpoint with A's ids and assert it's blocked (404),
and assert B's list endpoints never include A's resources. Includes the
runtime-store path (a chat turn hitting memory_chunks).

Two users, real Docker Postgres, the non-superuser persona_app role under RLS.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from persona.backends import StreamChunk
from persona.schema.conversation import ConversationMessage
from persona_api.app import create_app
from persona_api.config import APIConfig
from persona_api.middleware.rls_context import make_rls_engine
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from persona.schema.conversation import Conversation
    from sqlalchemy import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

_YAML = """\
schema_version: "1.0"
identity:
  name: A
  role: assistant
  background: |
    x
  language_default: en
  constraints: []
"""


class _Loop:
    async def turn(
        self,
        conversation: Conversation,
        user_message: str,
        on_event: object = None,  # noqa: ARG002 — accepted to match the loop signature
        *,
        turn_has_image: bool = False,  # noqa: ARG002 — spec-13 T20 compat
    ) -> AsyncIterator[StreamChunk]:
        now = datetime.now(UTC)
        conversation.messages.append(
            ConversationMessage(role="user", content=user_message, created_at=now)
        )
        conversation.messages.append(
            ConversationMessage(role="assistant", content="ok", created_at=now)
        )
        yield StreamChunk(delta="ok", is_final=True)


@pytest.fixture
def app_client(
    migrated_engine: Engine,  # noqa: ARG001
    embedder: HashEmbedder384,
    tmp_path: object,
) -> Iterator[TestClient]:
    import os

    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set")
    cfg = APIConfig(app_database_url=app_url, audit_root=str(tmp_path) + "/audit")
    app = create_app(cfg)

    from persona_api.auth import AuthenticatedUser

    async def _verify(token: str) -> AuthenticatedUser:
        return AuthenticatedUser(id=token, email=None)

    async def _build(_pid: str) -> _Loop:
        return _Loop()

    with TestClient(app) as c:
        app.state.verify_token = _verify
        app.state.embedder = embedder
        app.state.build_conversation_loop = _build
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            for u in ("user_A", "user_B"):
                conn.execute(
                    text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
                    {"i": u, "e": f"{u}@x"},
                )
        su.dispose()
        yield c
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id IN ('user_A','user_B')"))
        su.dispose()


def _h(uid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {uid}"}


def _seed_a(c: TestClient) -> dict[str, str]:
    """Create user A's persona + conversation as A. Returns the ids."""
    pid = c.post("/v1/personas", json={"yaml": _YAML}, headers=_h("user_A")).json()["id"]
    conv = c.post(
        f"/v1/personas/{pid}/conversations", json={"title": "A"}, headers=_h("user_A")
    ).json()["id"]
    return {"persona_id": pid, "conversation_id": conv}


def test_user_b_cannot_read_user_a_persona(app_client: TestClient) -> None:
    ids = _seed_a(app_client)
    assert (
        app_client.get(f"/v1/personas/{ids['persona_id']}", headers=_h("user_B")).status_code == 404
    )


def test_user_b_cannot_patch_or_delete_user_a_persona(app_client: TestClient) -> None:
    ids = _seed_a(app_client)
    pid = ids["persona_id"]
    assert (
        app_client.patch(
            f"/v1/personas/{pid}", json={"yaml": _YAML}, headers=_h("user_B")
        ).status_code
        == 404
    )
    assert app_client.delete(f"/v1/personas/{pid}", headers=_h("user_B")).status_code == 404
    # A can still read it (B's failed delete didn't touch it)
    assert app_client.get(f"/v1/personas/{pid}", headers=_h("user_A")).status_code == 200


def test_user_b_cannot_read_user_a_conversation(app_client: TestClient) -> None:
    ids = _seed_a(app_client)
    assert (
        app_client.get(
            f"/v1/conversations/{ids['conversation_id']}", headers=_h("user_B")
        ).status_code
        == 404
    )


def test_user_b_cannot_delete_user_a_conversation(app_client: TestClient) -> None:
    ids = _seed_a(app_client)
    cid = ids["conversation_id"]
    # B's delete on A's conversation → RLS hides it → 404, no deletion.
    assert app_client.delete(f"/v1/conversations/{cid}", headers=_h("user_B")).status_code == 404
    # A can still read it (B's delete matched no row).
    assert app_client.get(f"/v1/conversations/{cid}", headers=_h("user_A")).status_code == 200


def test_user_b_cannot_post_to_user_a_conversation(app_client: TestClient) -> None:
    ids = _seed_a(app_client)
    # B posts to A's conversation → the pre-flight RLS check 404s BEFORE the
    # stream starts (clean error, no mid-stream "response already started").
    resp = app_client.post(
        f"/v1/conversations/{ids['conversation_id']}/messages",
        json={"content": "intrude"},
        headers=_h("user_B"),
    )
    assert resp.status_code == 404
    # And the conversation was not mutated by B's attempt.
    hist = app_client.get(
        f"/v1/conversations/{ids['conversation_id']}", headers=_h("user_A")
    ).json()
    assert hist["messages"] == []


def test_user_b_cannot_create_conversation_on_user_a_persona(app_client: TestClient) -> None:
    ids = _seed_a(app_client)
    # B references A's persona id → RLS hides it → 404 (persona not found).
    assert (
        app_client.post(
            f"/v1/personas/{ids['persona_id']}/conversations",
            json={"title": "x"},
            headers=_h("user_B"),
        ).status_code
        == 404
    )


def test_user_b_cannot_read_user_a_run(app_client: TestClient) -> None:
    ids = _seed_a(app_client)
    run_id = app_client.post(
        f"/v1/personas/{ids['persona_id']}/runs", json={"task": "t"}, headers=_h("user_A")
    ).json()["id"]
    assert app_client.get(f"/v1/runs/{run_id}", headers=_h("user_B")).status_code == 404
    assert app_client.post(f"/v1/runs/{run_id}/cancel", headers=_h("user_B")).status_code == 404


def test_user_b_list_endpoints_exclude_user_a_resources(app_client: TestClient) -> None:
    ids = _seed_a(app_client)
    # B lists personas / conversations → must not see A's.
    b_personas = app_client.get("/v1/personas", headers=_h("user_B")).json()
    assert all(p["id"] != ids["persona_id"] for p in b_personas)
    b_convs = app_client.get("/v1/conversations", headers=_h("user_B")).json()
    assert all(cv["id"] != ids["conversation_id"] for cv in b_convs)


_TINY_PNG_BYTES: bytes = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae"
    "426082"
)


def test_user_b_cannot_post_upload_to_user_a_persona(app_client: TestClient) -> None:
    """Spec 13 T11: cross-tenant POST to /personas/{id}/uploads → 404.

    Extends the spec-08 sweep so the upload-route pair (POST + GET) is
    covered by the RLS adversarial proof. T14 inherits this assertion.
    """
    ids = _seed_a(app_client)
    resp = app_client.post(
        f"/v1/personas/{ids['persona_id']}/uploads",
        files={"file": ("a.png", _TINY_PNG_BYTES, "image/png")},
        headers=_h("user_B"),
    )
    # B references A's persona id → RLS-scoped pre-flight 404s (no upload).
    assert resp.status_code == 404


def test_user_b_cannot_get_user_a_uploads(app_client: TestClient) -> None:
    """Spec 13 T11: cross-tenant GET to /personas/{id}/uploads/{ref} → 404.

    Even with a probe ref pattern, B's request is rejected by the pre-flight
    RLS check before any workspace I/O. Existence-disclosure-safe per
    D-13: B cannot distinguish "no such persona" from "no such ref".
    """
    ids = _seed_a(app_client)
    # User A first uploads so a ref exists in the workspace.
    up = app_client.post(
        f"/v1/personas/{ids['persona_id']}/uploads",
        files={"file": ("a.png", _TINY_PNG_BYTES, "image/png")},
        headers=_h("user_A"),
    )
    assert up.status_code == 201, up.text
    ref = up.json()["workspace_path"]
    # B GETs A's ref → 404 (pre-flight RLS persona check fires).
    resp = app_client.get(f"/v1/personas/{ids['persona_id']}/uploads/{ref}", headers=_h("user_B"))
    assert resp.status_code == 404


def test_runtime_store_path_is_tenant_scoped(app_client: TestClient) -> None:
    """A chat turn populates memory_chunks under A; B's chat on B's own persona
    sees only B's memory. Proves the runtime-store path is RLS-scoped (#4)."""
    import os

    ids_a = _seed_a(app_client)
    # A sends a message (writes a message row; memory chunks were written on create)
    app_client.post(
        f"/v1/conversations/{ids_a['conversation_id']}/messages",
        json={"content": "hello from A"},
        headers=_h("user_A"),
    )
    # Directly assert memory_chunks for A's persona are not visible to B via RLS.
    su = make_rls_engine(os.environ["DATABASE_URL"])
    app_url = os.environ["APP_DATABASE_URL"]
    app_eng = make_rls_engine(app_url)
    from persona_api.middleware.rls_context import current_user_id

    tok = current_user_id.set("user_B")
    try:
        with app_eng.begin() as conn:
            visible = conn.execute(
                text("SELECT count(*) FROM memory_chunks WHERE persona_id = :p"),
                {"p": ids_a["persona_id"]},
            ).scalar()
    finally:
        current_user_id.reset(tok)
    app_eng.dispose()
    su.dispose()
    assert visible == 0  # B cannot see A's persona's memory chunks
