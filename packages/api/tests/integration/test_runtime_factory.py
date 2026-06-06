"""RuntimeFactory builds a working real ConversationLoop (spec 08, T10).

Proves the composition root actually wires a runnable loop: a RuntimeFactory
with a tier registry returning an inline scripted backend builds a
ConversationLoop for a real persona (memory in Postgres under RLS), and one
turn streams a response + persists. This is the real-loop counterpart to T08's
scripted-loop test — it exercises build_conversation_loop end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from persona.backends import StreamChunk, TokenUsage
from persona.stores.postgres import PostgresBackend
from persona_api.middleware.rls_context import current_user_id, make_rls_engine
from persona_api.services.runtime_factory import RuntimeFactory
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona.backends import ToolSpec
    from persona.schema.conversation import ConversationMessage
    from sqlalchemy import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

_YAML = """\
schema_version: "1.0"
identity:
  name: Astrid
  role: assistant
  background: |
    A helper.
  language_default: en
  constraints: []
self_facts:
  - fact: knows things
    confidence: 1.0
"""


class _ScriptedBackend:
    """Minimal ChatBackend: streams a fixed reply, no tools."""

    provider_name = "anthropic"
    model_name = "scripted"
    max_tokens = 4096

    @property
    def supports_native_tools(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        return False

    async def chat(self, messages: list[ConversationMessage], **_: object) -> object:  # noqa: ARG002
        raise NotImplementedError

    async def chat_stream(
        self,
        messages: list[ConversationMessage],  # noqa: ARG002
        *,
        tools: list[ToolSpec] | None = None,  # noqa: ARG002
        **_: object,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="Hei! ", is_final=False)
        yield StreamChunk(
            delta="Jeg er Astrid.",
            is_final=True,
            usage=TokenUsage(prompt_tokens=12, completion_tokens=7, total_tokens=19),
        )


class _ScriptedRegistry:
    """A TierRegistry stand-in returning the scripted backend for any tier."""

    def __init__(self) -> None:
        self._b = _ScriptedBackend()

    def get(self, _tier_name: str) -> _ScriptedBackend:
        return self._b

    async def aclose(self) -> None:
        pass


def _seed_persona(
    database_url: str, embedder: HashEmbedder384, owner: str, persona_id: str
) -> None:
    su = make_rls_engine(database_url)
    with su.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
            {"i": owner, "e": f"{owner}@x"},
        )
        conn.execute(
            text("INSERT INTO personas (id, owner_id, yaml) VALUES (:i, :o, :y)"),
            {"i": persona_id, "o": owner, "y": _YAML},
        )
    # populate memory under the owner's scope so the loop's retrieval works
    backend = PostgresBackend(engine=su, embedder=embedder)
    from persona.schema.chunks import PersonaChunk

    backend.upsert(
        persona_id=persona_id,
        store_kind="self_facts",
        chunks=[
            PersonaChunk(
                id=f"{persona_id}::self_facts::0000",
                text="self_fact: knows things",
                metadata={},
                created_at=datetime.now(UTC),
            )
        ],
    )
    su.dispose()


@pytest.mark.asyncio
async def test_factory_builds_a_runnable_conversation_loop(
    migrated_engine: Engine,  # noqa: ARG001
    embedder: HashEmbedder384,
) -> None:
    import os

    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set")
    su_url = os.environ["DATABASE_URL"]
    owner, persona_id = "user_t10", "persona_t10"
    _seed_persona(su_url, embedder, owner, persona_id)

    rls_engine = make_rls_engine(app_url)
    factory = RuntimeFactory(
        rls_engine=rls_engine,
        embedder=embedder,
        tier_registry=_ScriptedRegistry(),  # type: ignore[arg-type]
        turn_log_writer=_NullTurnLog(),  # type: ignore[arg-type]
        audit_root=Path("/tmp/persona-t10-audit"),
    )

    token = current_user_id.set(owner)  # scope the factory's store engine to the owner
    try:
        loop = await factory.build_conversation_loop(persona_id)
        from persona.schema.conversation import Conversation

        conv = Conversation(conversation_id="c_t10", persona_id=persona_id, messages=[])
        deltas = [c.delta async for c in loop.turn(conv, "Hvem er du?")]
        assert "".join(deltas) == "Hei! Jeg er Astrid."
        # the loop appended user + assistant to the conversation
        assert [m.role for m in conv.messages] == ["user", "assistant"]
    finally:
        current_user_id.reset(token)
        rls_engine.dispose()
        _cleanup(su_url, owner)


class _NullTurnLog:
    def write(self, _log: object) -> None:
        pass


def _cleanup(su_url: str, owner: str) -> None:
    su = make_rls_engine(su_url)
    with su.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": owner})
    su.dispose()
