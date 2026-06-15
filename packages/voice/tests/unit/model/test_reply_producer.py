"""Unit tests for persona_voice.model.VoiceModelReplyProducer (spec V5 T4).

The anti-bypass checkpoint (criterion 1, the product-killer line): the prompt the
voice model receives carries the FULL persona conditioning — identity,
constraints, and retrieved typed memory — via the shared ``PromptBuilder``. Plus:
the producer streams spoken text only (never reasoning), and stamps first-token
latency into the tracker + the VoiceLog listener.
"""

# ruff: noqa: ANN401, ARG001, ARG002 — test doubles with intentionally loose signatures.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from persona.backends import StreamChunk, TokenUsage
from persona.backends.types import ToolCallDelta
from persona.history import ConversationHistoryManager
from persona.schema.chunks import PersonaChunk
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona, PersonaIdentity, RoutingConfig
from persona.schema.tools import ToolResult
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.routing.latency import FirstTokenLatencyTracker
from persona_runtime.tier import TierConfig, TierRegistry
from persona_voice.loop.streaming import Transcript
from persona_voice.model import (
    DeferredArtifact,
    VoiceModelReplyProducer,
    VoiceTurnContext,
    VoiceTurnRecorder,
)
from persona_voice.turn_taking.heard_words import BargedReply


def _chunk(text: str, meta: dict[str, str] | None = None) -> PersonaChunk:
    return PersonaChunk(
        id=f"id-{abs(hash(text)) % 10000}",
        text=text,
        metadata=meta or {},
        created_at=datetime.now(UTC),
    )


class _FakeStore:
    def __init__(
        self,
        *,
        all_chunks: list[PersonaChunk] | None = None,
        query_chunks: list[PersonaChunk] | None = None,
    ) -> None:
        self._all = all_chunks or []
        self._query = query_chunks or []

    def write(self, persona_id: str, chunks: list[PersonaChunk], **kwargs: Any) -> None:
        self._all.extend(chunks)

    def query(self, persona_id: str, query: str, top_k: int, **filters: Any) -> list[PersonaChunk]:
        return list(self._query[:top_k])

    def get_all(self, persona_id: str, *, include_superseded: bool = False) -> list[PersonaChunk]:
        return list(self._all)

    def delete(self, persona_id: str) -> None:
        return None


class _ScriptedBackend:
    """A streaming ChatBackend double that captures the prompt + yields chunks."""

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
        identity=PersonaIdentity(
            name="Astrid",
            role="Norwegian tenancy law assistant",
            background="Knows husleieloven.",
            constraints=["Never give binding legal advice."],
        ),
        routing=RoutingConfig(tier_for_generation="frontier"),  # override → no router needed
    )


def _context(
    backend: object,
    *,
    tracker: FirstTokenLatencyTracker | None = None,
    toolbox: object | None = None,
) -> VoiceTurnContext:
    from persona.backends import BackendConfig

    stores = {
        "identity": _FakeStore(all_chunks=[_chunk("I am Astrid, a tenancy assistant.")]),
        "self_facts": _FakeStore(query_chunks=[_chunk("I specialise in tenancy law.")]),
        "worldview": _FakeStore(query_chunks=[_chunk("Tenants have strong protections.")]),
        "episodic": _FakeStore(query_chunks=[_chunk("Last time we discussed mould.")]),
    }
    cfg = BackendConfig(provider="anthropic", model="test-model", api_key=None)  # type: ignore[arg-type]
    registry = TierRegistry({"frontier": TierConfig(name="frontier", backend_config=cfg)})
    registry._cache = {"frontier": backend}  # type: ignore[assignment,dict-item]  # noqa: SLF001
    return VoiceTurnContext(
        persona=_persona(),
        stores=stores,  # type: ignore[arg-type]
        conversation=Conversation(conversation_id="c1", persona_id="astrid", messages=[]),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        latency_tracker=tracker,
        toolbox=toolbox,  # type: ignore[arg-type]
    )


def _final() -> StreamChunk:
    return StreamChunk(
        delta="",
        is_final=True,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


async def _drain(producer: VoiceModelReplyProducer, text: str = "What are my rights?") -> list[str]:
    stream = await producer(Transcript(is_final=True, text=text, confidence=1.0))
    return [tok async for tok in stream]


class TestAntiBypass:
    """Criterion 1 — the model receives the FULL persona conditioning (no bypass)."""

    @pytest.mark.asyncio
    async def test_prompt_carries_identity_constraints_and_memory(self) -> None:
        backend = _ScriptedBackend([StreamChunk(delta="Hello"), _final()])
        producer = VoiceModelReplyProducer(_context(backend))

        await _drain(producer)

        assert backend.captured_prompt is not None
        system = backend.captured_prompt[0].content
        assert "You are Astrid" in system  # identity
        assert "You must NOT:" in system  # constraints enforced
        assert "Never give binding legal advice." in system
        assert "Relevant facts about yourself:" in system  # self_facts retrieved
        assert "Your views:" in system  # worldview retrieved
        assert "From earlier conversations:" in system  # episodic retrieved
        # The transcribed user turn is the last message.
        assert backend.captured_prompt[-1].role == "user"
        assert backend.captured_prompt[-1].content == "What are my rights?"


class TestStreaming:
    @pytest.mark.asyncio
    async def test_yields_reply_tokens_in_order(self) -> None:
        backend = _ScriptedBackend(
            [StreamChunk(delta="Hel"), StreamChunk(delta="lo"), StreamChunk(delta="!"), _final()]
        )
        producer = VoiceModelReplyProducer(_context(backend))
        assert await _drain(producer) == ["Hel", "lo", "!"]

    @pytest.mark.asyncio
    async def test_reasoning_is_never_spoken(self) -> None:
        # A reasoning-only chunk (no delta) must not be yielded to TTS.
        backend = _ScriptedBackend(
            [
                StreamChunk(delta="", reasoning="(thinking hard)"),
                StreamChunk(delta="Answer"),
                _final(),
            ]
        )
        producer = VoiceModelReplyProducer(_context(backend))
        assert await _drain(producer) == ["Answer"]


class TestFirstTokenStamping:
    @pytest.mark.asyncio
    async def test_records_latency_into_tracker(self) -> None:
        tracker = FirstTokenLatencyTracker()
        backend = _ScriptedBackend([StreamChunk(delta="Hi"), _final()])
        producer = VoiceModelReplyProducer(_context(backend, tracker=tracker))

        await _drain(producer)

        assert tracker.sample_count("test-model") == 1

    @pytest.mark.asyncio
    async def test_notifies_first_token_listener_once_with_timestamp(self) -> None:
        stamps: list[datetime] = []
        fixed = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
        backend = _ScriptedBackend([StreamChunk(delta="a"), StreamChunk(delta="b"), _final()])
        producer = VoiceModelReplyProducer(
            _context(backend), first_token_listener=stamps.append, clock=lambda: fixed
        )

        await _drain(producer)

        assert stamps == [fixed]  # exactly once, on the first token


class _ParkingBackend:
    """A streaming backend that parks after the first token (cancellable shape).

    Mirrors the Spec 02 backend's ``async with provider.stream()`` close-on-cancel:
    the ``finally`` sets ``closed`` exactly as ``__aexit__`` would, so a barge-in
    cancel mid-stream proves the stream closes cleanly (R-V5-4 / criterion 5).
    """

    provider_name = "anthropic"
    model_name = "test-model"
    supports_native_tools = False
    supports_vision = False

    def __init__(self) -> None:
        self.closed = False
        self.emitted: list[str] = []
        self.first_emitted = asyncio.Event()
        self.release = asyncio.Event()

    async def chat_stream(
        self,
        messages: list[Any],
        *,
        tools: Any = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: Any = None,
    ) -> AsyncIterator[StreamChunk]:
        try:
            self.emitted.append("Hel")
            yield StreamChunk(delta="Hel")
            self.first_emitted.set()
            await self.release.wait()  # park mid-stream (cancellable point)
            for token in ("lo", "!"):
                self.emitted.append(token)
                yield StreamChunk(delta=token)
            yield _final()
        finally:
            self.closed = True  # the clean-close the real `async with` guarantees


class TestCancellation:
    """R-V5-4 / criterion 5 — cancel mid-generation stops fast + closes clean."""

    @pytest.mark.asyncio
    async def test_cancel_mid_stream_stops_and_closes_cleanly(self) -> None:
        backend = _ParkingBackend()
        producer = VoiceModelReplyProducer(_context(backend))
        spoken: list[str] = []

        async def run() -> None:
            stream = await producer(Transcript(is_final=True, text="hi", confidence=1.0))
            async for token in stream:
                spoken.append(token)

        task = asyncio.create_task(run())
        await asyncio.wait_for(backend.first_emitted.wait(), timeout=1.0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert spoken == ["Hel"]  # only the spoken-so-far prefix
        assert backend.emitted == ["Hel"]  # the remainder was never generated
        assert backend.closed is True  # the provider stream closed cleanly


# ----- T7 voice tools ------------------------------------------------------


class _MultiRoundBackend:
    """A streaming backend that serves a different chunk list per chat_stream call."""

    provider_name = "anthropic"
    model_name = "test-model"
    supports_native_tools = True
    supports_vision = False

    def __init__(self, rounds: list[list[StreamChunk]]) -> None:
        self._rounds = rounds
        self._call = 0

    async def chat_stream(
        self,
        messages: list[Any],
        *,
        tools: Any = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: Any = None,
    ) -> AsyncIterator[StreamChunk]:
        chunks = self._rounds[self._call]
        self._call += 1
        for chunk in chunks:
            yield chunk


def _tool_call_chunk(name: str, args: str) -> StreamChunk:
    return StreamChunk(
        delta="", tool_call_delta=ToolCallDelta(call_id="c1", name_delta=name, arguments_delta=args)
    )


@tool(name="web_search", description="Search the web.")
async def _web_search(query: str) -> ToolResult:
    return ToolResult(tool_name="web_search", content=f"results for {query}")


@tool(name="web_search", description="A slow search.")
async def _slow_web_search(query: str) -> ToolResult:
    await asyncio.sleep(1.0)
    return ToolResult(tool_name="web_search", content="late")


@tool(name="code_execution", description="Run code.")
async def _code_execution(code: str) -> ToolResult:
    msg = "a deferred tool must never run on the live voice path"
    raise RuntimeError(msg)


class TestVoiceToolsConversational:
    """Criterion 8 — a voice-viable tool works conversationally (preamble + result)."""

    @pytest.mark.asyncio
    async def test_voice_viable_tool_narrates_then_returns_result(self) -> None:
        backend = _MultiRoundBackend(
            [
                [_tool_call_chunk("web_search", '{"query": "rights"}'), _final()],
                [StreamChunk(delta="You have strong rights."), _final()],
            ]
        )
        toolbox = Toolbox([_web_search], allow_list=None)  # type: ignore[list-item]
        producer = VoiceModelReplyProducer(_context(backend, toolbox=toolbox))

        out = await _drain(producer)

        assert "Let me look that up." in out  # the first-class preamble (D-V5-5)
        assert "You have strong rights." in out  # the tool ran → re-prompt answer
        # The conversation stays alive: preamble spoken before the result.
        assert out.index("Let me look that up.") < out.index("You have strong rights.")

    @pytest.mark.asyncio
    async def test_deferred_tool_is_acknowledged_not_executed(self) -> None:
        backend = _MultiRoundBackend(
            [[_tool_call_chunk("code_execution", '{"code": "x"}'), _final()]]
        )
        toolbox = Toolbox([_code_execution], allow_list=None)  # type: ignore[list-item]
        captured: list[DeferredArtifact] = []
        producer = VoiceModelReplyProducer(
            _context(backend, toolbox=toolbox), deferred_artifact_listener=captured.append
        )

        # If the deferred tool were dispatched it would raise — reaching here proves
        # it was acknowledged, not executed (D-V5-4).
        out = await _drain(producer)

        assert "I'll prepare that and have it ready for you after our call." in out
        assert len(captured) == 1
        assert captured[0].tool_name == "code_execution"

    @pytest.mark.asyncio
    async def test_voice_tool_overrun_speaks_graceful_overflow(self) -> None:
        backend = _MultiRoundBackend([[_tool_call_chunk("web_search", '{"query": "x"}'), _final()]])
        toolbox = Toolbox([_slow_web_search], allow_list=None)  # type: ignore[list-item]
        producer = VoiceModelReplyProducer(_context(backend, toolbox=toolbox), tool_timeout_s=0.01)

        out = await _drain(producer)

        assert "Let me look that up." in out  # preamble still spoken
        assert "This is taking a moment — I'll follow up on that shortly." in out  # bound hit


class TestUnifiedMemoryWiring:
    """T8 — the producer notes the user turn so the recorder can correlate it."""

    @pytest.mark.asyncio
    async def test_producer_notes_user_so_commit_writes_the_pair(self) -> None:
        backend = _ScriptedBackend([StreamChunk(delta="Noted."), _final()])
        ctx = _context(backend)
        recorder = VoiceTurnRecorder(ctx)
        producer = VoiceModelReplyProducer(ctx, turn_recorder=recorder)

        await _drain(producer, text="remember my cat Milo")
        await recorder.on_reply_committed(
            BargedReply(heard_text="Noted.", truncated=False, token_count=1)
        )

        written = ctx.stores["episodic"].get_all("astrid", include_superseded=True)
        assert any("remember my cat Milo" in c.text and "Noted." in c.text for c in written)
