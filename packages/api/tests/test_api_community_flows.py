"""Community-edition HTTP core-flow smoke suite (R1-D-4, the anchor).

The durable guard the community path was missing: it boots community with NO
cloud config (no Postgres, no Clerk, **no model key**) on SQLite + Chroma and
drives the core flows through the REAL HTTP surface — the path the original
``test_api_community_boot.py`` deliberately side-stepped (it exercised
persona-create via the service layer to avoid the capability-hydration edge).

Runs in the default ``pytest`` invocation (no ``integration``/``external``
marker, no Docker) so a cloud-assuming regression fails loudly in CI — in
particular, a future ``supports_vision_for``-on-read regression (reverting R1-D-1
to instantiate a backend on persona detail) re-introduces the keyless 500 and
trips these tests.

Scenario under test: ``PERSONA_PROVIDER``/``PERSONA_MODEL`` set but no API key —
the deferred-config path that builds a live ``tier_registry`` whose backend
cannot construct keyless. That is the exact shape that 500'd persona
create/get/list before R1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from persona.backends import StreamChunk, TokenUsage
from persona.schema.conversation import ConversationMessage
from persona_api.app import create_app
from persona_api.config import APIConfig, Edition

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from persona.schema.conversation import Conversation
    from persona.stores.embedder import Embedder
    from persona_runtime.agentic.events import RunEvent
    from persona_runtime.prompt import DocumentContext

_YAML = (
    "schema_version: '1.0'\n"
    "identity:\n"
    "  name: Sigrid\n"
    "  role: research assistant\n"
    "  background: A research assistant built to help with literature reviews.\n"
    "self_facts:\n"
    "  - fact: I was built to help with literature reviews.\n"
)

_MODEL_KEY_VARS = (
    "PERSONA_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
)


def _community_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    embedder: Embedder,
    *,
    provider: str,
    model: str,
) -> FastAPI:
    """Build a community app with a CONTROLLED keyless model config.

    ``PERSONA_PROVIDER``/``PERSONA_MODEL`` set + every model key removed ⇒ the
    single-backend fallback builds a live ``tier_registry`` whose backend would
    raise ``AuthenticationError`` if instantiated — so persona detail must
    answer capabilities statically (R1-D-1), never 500.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("PERSONA_PROVIDER", provider)
    monkeypatch.setenv("PERSONA_MODEL", model)
    for var in _MODEL_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    # Torch-free deterministic embedder (the real one pulls torch; these tests
    # assert HTTP flow + capability hydration, not embedding quality).
    from persona_api.services import persona_service

    monkeypatch.setattr(persona_service, "default_embedder", lambda *_a, **_k: embedder)
    config = APIConfig(
        edition=Edition.community,
        community_db_path=tmp_path / "community.db",
        community_memory_path=tmp_path / "chroma",
        workspace_root=tmp_path / "work",
        audit_root=str(tmp_path / "audit"),
    )
    return create_app(config)


class _ScriptedLoop:
    """ConversationLoop stand-in: yields chunks + appends messages like the real
    loop, with NO model backend — proves the community chat HTTP path works
    keyless on SQLite/Chroma (mirrors integration/test_conversations.py)."""

    def __init__(self, reply: str = "Hello there!") -> None:
        self._reply = reply

    async def turn(
        self,
        conversation: Conversation,
        user_message: str,
        on_event: Callable[[RunEvent], Awaitable[None]] | None = None,  # noqa: ARG002 — real-loop kwarg compat
        *,
        turn_has_image: bool = False,  # noqa: ARG002 — real-loop kwarg compat
        images: list[object] | None = None,  # noqa: ARG002
        documents: list[object] | None = None,  # noqa: ARG002
        document_context: DocumentContext | None = None,  # noqa: ARG002
    ) -> AsyncIterator[StreamChunk]:
        now = datetime.now(UTC)
        conversation.messages.append(
            ConversationMessage(role="user", content=user_message, created_at=now)
        )
        yield StreamChunk(delta=self._reply[:5], is_final=False)
        yield StreamChunk(delta=self._reply[5:], is_final=False)
        conversation.messages.append(
            ConversationMessage(role="assistant", content=self._reply, created_at=now)
        )
        yield StreamChunk(
            delta="",
            is_final=True,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


# ----- read path: capability hydration is keyless + statically correct --------


@pytest.mark.parametrize(
    ("provider", "model", "expected_vision"),
    [
        ("deepseek", "deepseek-chat", False),  # keyless registry that previously 500'd
        ("anthropic", "claude-sonnet-4-6", True),  # static vision-capable, still keyless
        ("ollama", "llava", False),  # R1-D-3 pin: env-config Ollama under-reports, no 500
    ],
)
def test_keyless_create_get_list_hydrate_capabilities_without_500(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    embedder: Embedder,
    provider: str,
    model: str,
    expected_vision: bool,
) -> None:
    app = _community_app(tmp_path, monkeypatch, embedder, provider=provider, model=model)
    with TestClient(app) as client:
        # The registry built keyless — the exact shape that used to 500 on read.
        assert getattr(client.app.state, "tier_registry", None) is not None

        create = client.post("/v1/personas", json={"yaml": _YAML})
        assert create.status_code == 201, create.text
        pid = create.json()["id"]
        # create returns PersonaDetail ⇒ capability hydration already ran here.
        assert create.json()["capabilities"]["vision"] is expected_vision

        detail = client.get(f"/v1/personas/{pid}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["capabilities"]["vision"] is expected_vision

        listed = client.get("/v1/personas")
        assert listed.status_code == 200, listed.text
        assert any(p["id"] == pid for p in listed.json())


# ----- chat path: community chat plumbing works keyless -----------------------


def test_keyless_chat_turn_streams_over_community_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    app = _community_app(
        tmp_path, monkeypatch, embedder, provider="deepseek", model="deepseek-chat"
    )
    with TestClient(app) as client:
        # Stub the loop builder so the turn runs with no model backend.
        async def _build_loop(_persona_id: str) -> _ScriptedLoop:
            return _ScriptedLoop()

        client.app.state.build_conversation_loop = _build_loop  # type: ignore[attr-defined]

        pid = client.post("/v1/personas", json={"yaml": _YAML}).json()["id"]
        conv = client.post(f"/v1/personas/{pid}/conversations", json={"title": ""})
        assert conv.status_code == 201, conv.text
        conv_id = conv.json()["id"]

        resp = client.post(f"/v1/conversations/{conv_id}/messages", json={"content": "hi there"})
        assert resp.status_code == 200, resp.text
        # The reply streamed back as SSE chunk events (split across two deltas).
        assert "event: chunk" in resp.text
        assert "Hello" in resp.text
        assert "there!" in resp.text


# ----- write path: keyless authoring should be a clean 503 (T3 deliverable) ---


def test_keyless_authoring_returns_503_not_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, embedder: Embedder
) -> None:
    # The route-local guard (R1-D-2) collapses the keyless backend
    # AuthenticationError to a clean 503 instead of leaking a 500.
    app = _community_app(
        tmp_path, monkeypatch, embedder, provider="anthropic", model="claude-sonnet-4-6"
    )
    with TestClient(app) as client:
        resp = client.post("/v1/personas/author", json={"description": "a helpful tutor"})
        assert resp.status_code == 503, resp.text
        assert resp.json()["error"] == "model_unavailable"
