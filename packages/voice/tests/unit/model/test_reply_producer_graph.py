"""T8 — the voice path actually uses the overlapped graph (K3-D-6 wiring).

Proves the voice half of the wiring end-to-end: the assembler injects a taken
GraphContext into the shared PromptBuilder (so a voice prompt renders graph
knowledge exactly as chat does), and the reply producer starts the owner-scoped
query at turn onset + takes it before assembly. The zero-serial overlap PRIMITIVE
itself is proven in test_graph_voice.py; here we prove the voice composition uses
it — the graph node a persona learned through another persona reaches the spoken
turn's prompt.
"""

# ruff: noqa: ANN401, ARG001, ARG002 — test doubles with intentionally loose signatures.

from __future__ import annotations

import dataclasses
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from persona.backends import StreamChunk, TokenUsage
from persona.history import ConversationHistoryManager
from persona.schema.chunks import PersonaChunk
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona, PersonaIdentity, RoutingConfig
from persona_runtime.prompt import (
    GraphContext,
    GraphKnowledgeItem,
    GraphRecency,
    PromptBuilder,
    RetrievedContext,
)
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry
from persona_voice.loop.streaming import Transcript
from persona_voice.model import VoiceModelReplyProducer, VoiceTurnContext
from persona_voice.model.prompt_assembler import VoicePromptAssembler


def _chunk(text: str) -> PersonaChunk:
    return PersonaChunk(
        id=f"id-{abs(hash(text)) % 10000}", text=text, metadata={}, created_at=datetime.now(UTC)
    )


def _known_graph() -> GraphContext:
    return GraphContext(
        items=(
            GraphKnowledgeItem(
                concept_name="focus struggles",
                content="Finds long study sessions hard to concentrate through.",
                recency=GraphRecency.A_WHILE_BACK,
                source_persona="kai",
            ),
        )
    )


class _FakeStore:
    def __init__(self, *, all_chunks: list[PersonaChunk] | None = None) -> None:
        self._all = all_chunks or []

    def write(self, persona_id: str, chunks: list[PersonaChunk], **kwargs: Any) -> None: ...

    def query(self, persona_id: str, query: str, top_k: int, **filters: Any) -> list[PersonaChunk]:
        return []

    def get_all(self, persona_id: str, *, include_superseded: bool = False) -> list[PersonaChunk]:
        # A small real cost so the threaded persona-retrieval gives the
        # concurrent graph query a genuine window to finish in (mirrors prod,
        # where the graph rides alongside the store I/O — D-K3-6).
        time.sleep(0.03)
        return list(self._all)

    def recent(self, persona_id: str, limit: int) -> list[PersonaChunk]:
        return []

    def delete(self, persona_id: str) -> None: ...


class _ScriptedBackend:
    provider_name = "anthropic"
    model_name = "test-model"
    supports_native_tools = False
    supports_vision = False

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self._chunks = chunks
        self.captured_prompt: list[Any] | None = None

    async def chat_stream(
        self,
        messages: list[Any],
        *,
        tools: Any = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: Any = None,
    ) -> AsyncIterator[StreamChunk]:
        self.captured_prompt = messages
        for chunk in self._chunks:
            yield chunk


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(name="Astrid", role="assistant", background="."),
        routing=RoutingConfig(tier_for_generation="frontier"),
    )


def _stores() -> dict[str, _FakeStore]:
    return {
        "identity": _FakeStore(all_chunks=[_chunk("I am Astrid.")]),
        "self_facts": _FakeStore(),
        "worldview": _FakeStore(),
        "episodic": _FakeStore(),
    }


def _context(backend: object, *, graph_retrieval: object | None = None) -> VoiceTurnContext:
    from persona.backends import BackendConfig

    cfg = BackendConfig(provider="anthropic", model="test-model", api_key=None)  # type: ignore[arg-type]
    registry = TierRegistry({"frontier": TierConfig(name="frontier", backend_config=cfg)})
    registry._cache = {"frontier": backend}  # type: ignore[assignment,dict-item]  # noqa: SLF001
    return VoiceTurnContext(
        persona=_persona(),
        stores=_stores(),  # type: ignore[arg-type]
        conversation=Conversation(conversation_id="c1", persona_id="astrid", messages=[]),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        graph_retrieval=graph_retrieval,  # type: ignore[arg-type]
    )


class TestAssemblerInjectsGraph:
    """The deterministic seam: a taken GraphContext reaches the voice prompt."""

    def test_build_injects_the_graph_into_the_voice_prompt(self) -> None:
        ctx = _context(_ScriptedBackend([]))
        assembler = VoicePromptAssembler(ctx)
        messages = assembler.build(
            "help me study", history=[], max_tokens=8000, graph=_known_graph()
        )
        system = messages[0].content
        assert "What you already know about this person:" in system
        assert "Finds long study sessions hard to concentrate through." in system

    def test_build_without_graph_is_graph_off(self) -> None:
        ctx = _context(_ScriptedBackend([]))
        assembler = VoicePromptAssembler(ctx)
        messages = assembler.build("hi", history=[], max_tokens=8000)
        assert "What you already know about this person:" not in messages[0].content

    def test_retrieved_context_is_frozen_so_model_copy_injection_is_safe(self) -> None:
        # The injection path uses RetrievedContext.model_copy — confirm it's frozen
        # (the pattern the assembler relies on).
        assert RetrievedContext.model_config["frozen"] is True


def _final() -> StreamChunk:
    return StreamChunk(
        delta="",
        is_final=True,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class TestProducerUsesOverlappedGraph:
    @pytest.mark.asyncio
    async def test_producer_uses_the_overlapped_graph_when_ready(self) -> None:
        # A near-instant graph query: by assembly time the overlapped task has
        # finished, so the node lands in the prompt the voice model receives.
        seen_query: list[str] = []

        def fast_graph(query: str) -> GraphContext:
            seen_query.append(query)
            return _known_graph()

        backend = _ScriptedBackend([StreamChunk(delta="ok"), _final()])
        producer = VoiceModelReplyProducer(_context(backend, graph_retrieval=fast_graph))

        stream = await producer(Transcript(is_final=True, text="help me study", confidence=1.0))
        _ = [tok async for tok in stream]

        assert seen_query == ["help me study"]  # the owner-scoped query ran, scoped to the turn
        assert backend.captured_prompt is not None
        system = backend.captured_prompt[0].content
        assert "Finds long study sessions hard to concentrate through." in system

    @pytest.mark.asyncio
    async def test_graph_off_when_not_wired_is_byte_clean(self) -> None:
        backend = _ScriptedBackend([StreamChunk(delta="ok"), _final()])
        producer = VoiceModelReplyProducer(_context(backend, graph_retrieval=None))
        stream = await producer(Transcript(is_final=True, text="hi", confidence=1.0))
        _ = [tok async for tok in stream]
        assert backend.captured_prompt is not None
        assert "What you already know about this person:" not in backend.captured_prompt[0].content

    def test_voice_turn_context_carries_graph_retrieval(self) -> None:
        ctx = _context(_ScriptedBackend([]), graph_retrieval=lambda _q: GraphContext())
        # dataclasses.replace works (frozen dc) — the field is a real composable.
        replaced = dataclasses.replace(ctx, graph_retrieval=None)
        assert replaced.graph_retrieval is None
