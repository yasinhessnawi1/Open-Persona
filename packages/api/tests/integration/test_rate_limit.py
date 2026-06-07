"""Rate-limiting integration: 429 + headers on the message endpoint (T09, §6 #3).

Drives the real app with a fake verifier + scripted loop + a LOW per-minute
message limit, and asserts the (limit+1)th request is 429 with X-RateLimit-*
headers, while allowed responses carry the headers too. Also exercises the
PostgresRateLimitStore (backend="postgres") against the real buckets table.
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
    from persona_runtime.prompt import DocumentContext
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
        document_context: DocumentContext | None = None,  # noqa: ARG002 — spec-14 compat
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
def client(
    migrated_engine: Engine,  # noqa: ARG001
    embedder: HashEmbedder384,
    tmp_path: object,
) -> Iterator[tuple[TestClient, str, str]]:
    import os

    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set")
    # postgres-backed limiter (the real buckets table) + a low message limit.
    cfg = APIConfig(
        app_database_url=app_url,
        audit_root=str(tmp_path) + "/audit",
        rate_limit_backend="postgres",
        rate_limit_messages=3,
    )
    app = create_app(cfg)

    from persona_api.auth import AuthenticatedUser

    async def _verify(token: str) -> AuthenticatedUser:
        return AuthenticatedUser(id=token, email=None)

    async def _build_loop(_pid: str) -> _Loop:
        return _Loop()

    uid = "user_t09"
    with TestClient(app) as c:
        app.state.verify_token = _verify
        app.state.embedder = embedder
        app.state.build_conversation_loop = _build_loop
        # Drop the lifespan-installed TierRegistry so the persona-detail
        # capabilities surface doesn't lazily instantiate a real chat backend
        # (AuthenticationError("missing API key") on CI without ANTHROPIC_API_KEY).
        if hasattr(app.state, "tier_registry"):
            app.state.tier_registry = None
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
                {"i": uid, "e": f"{uid}@x"},
            )
            # clear any leftover buckets for determinism
            conn.execute(text("DELETE FROM rate_limit_buckets WHERE user_id = :i"), {"i": uid})
        su.dispose()
        resp = c.post(
            "/v1/personas", json={"yaml": _YAML}, headers={"Authorization": f"Bearer {uid}"}
        )
        pid = resp.json()["id"]
        conv = c.post(
            f"/v1/personas/{pid}/conversations",
            json={"title": "t"},
            headers={"Authorization": f"Bearer {uid}"},
        ).json()["id"]
        yield c, uid, conv
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(text("DELETE FROM rate_limit_buckets WHERE user_id = :i"), {"i": uid})
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": uid})
        su.dispose()


def test_exceeding_message_limit_returns_429_with_headers(
    client: tuple[TestClient, str, str],
) -> None:
    c, uid, conv = client
    h = {"Authorization": f"Bearer {uid}"}
    # limit is 3: first 3 allowed, 4th → 429
    for i in range(3):
        r = c.post(f"/v1/conversations/{conv}/messages", json={"content": f"m{i}"}, headers=h)
        assert r.status_code == 200, f"request {i} unexpectedly {r.status_code}"
        assert r.headers["X-RateLimit-Limit"] == "3"
        assert "X-RateLimit-Remaining" in r.headers
        assert "X-RateLimit-Reset" in r.headers
    r = c.post(f"/v1/conversations/{conv}/messages", json={"content": "over"}, headers=h)
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limit_exceeded"
    assert r.headers["X-RateLimit-Limit"] == "3"
    assert r.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in r.headers
