"""Unit tests for the chat-loop proactive question injection — spec 21 T06.

Verifies the PRE-generation decision point (D-05-12 ordering): an ambiguous
message asks a 3+1 question and skips generation; a clear message is byte-for-
byte unchanged; a gated/suppressed signal injects a stated-assumption nudge and
generates once (D-21-18); the autonomy level gates what is asked; an answer turn
is not re-detected; and an equivalent question is deduped (D-21-6).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from _fakes import FakeStore, ScriptedBackend, ScriptedRound  # type: ignore[import-not-found]
from persona.backends import BackendConfig
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation, ConversationMessage
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolResult
from persona.skills import SkillInjector, SkillScanner
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime.logging import MemoryTurnLogWriter
from persona_runtime.loop import ConversationLoop
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona.backends import StreamChunk
    from persona_runtime.agentic.events import RunEvent

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


@tool(name="echo", description="Echo.")
async def _echo(message: str) -> ToolResult:
    return ToolResult(tool_name="echo", content=message, is_error=False)


def _persona(autonomy: str = "cautious") -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(name="Astrid", role="assistant", background="bg"),
        autonomy=autonomy,  # type: ignore[arg-type]
    )


class _CapturingBackend(ScriptedBackend):
    """Records the prompt passed to the last streamed round (for nudge assertions)."""

    last_messages: list[ConversationMessage]

    def __init__(self, rounds: list[ScriptedRound]) -> None:
        super().__init__(rounds)
        self.last_messages = []

    async def chat_stream(
        self,
        messages: list[ConversationMessage],
        **kwargs: Any,  # noqa: ANN401 — test passthrough mirroring the backend signature
    ) -> AsyncIterator[StreamChunk]:
        self.last_messages = list(messages)
        async for chunk in super().chat_stream(messages, **kwargs):
            yield chunk


def _make_loop(
    backend: ScriptedBackend, *, persona: Persona | None = None
) -> tuple[ConversationLoop, dict[str, FakeStore]]:
    stores = {
        "identity": FakeStore(),
        "self_facts": FakeStore(),
        "worldview": FakeStore(),
        "episodic": FakeStore(),
    }
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]  # noqa: SLF001
    loop = ConversationLoop(
        persona=persona or _persona(),
        stores=stores,  # type: ignore[arg-type]
        toolbox=Toolbox([_echo], allow_list=None),  # type: ignore[arg-type]
        skill_scanner=SkillScanner([]),
        skill_injector=SkillInjector(),
        scanned_skills=[],
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        turn_log_writer=MemoryTurnLogWriter(),
    )
    return loop, stores


def _conv(messages: list[ConversationMessage] | None = None) -> Conversation:
    return Conversation(conversation_id="c1", persona_id="astrid", messages=messages or [])


async def _collect(
    loop: ConversationLoop, conv: Conversation, message: str
) -> tuple[list[StreamChunk], list[RunEvent]]:
    events: list[RunEvent] = []

    async def on_event(ev: RunEvent) -> None:
        events.append(ev)

    chunks = [c async for c in loop.turn(conv, message, on_event=on_event)]
    return chunks, events


class TestAsksOnAmbiguity:
    @pytest.mark.asyncio
    async def test_ambiguous_cautious_asks_and_skips_generation(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="should not be generated")])
        loop, stores = _make_loop(backend, persona=_persona("cautious"))
        conv = _conv()

        chunks, events = await _collect(loop, conv, "draft a complaint")

        asking = [e for e in events if e.type == "asking_user"]
        assert len(asking) == 1
        assert backend.chat_stream_calls == 0  # generation NOT called
        assert chunks[-1].is_final is True
        # Question persisted as a tagged assistant turn; no episodic write.
        assert conv.messages[-1].role == "assistant"
        assert conv.messages[-1].metadata.get("proactive_question") == "true"
        assert stores["episodic"].writes == []

    @pytest.mark.asyncio
    async def test_asking_user_event_carries_3_plus_1_payload(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="x")])
        loop, _ = _make_loop(backend, persona=_persona("cautious"))

        _chunks, events = await _collect(loop, _conv(), "delete everything")

        ask = next(e for e in events if e.type == "asking_user")
        assert len(ask.data["options"]) == 3
        assert ask.data["allow_free_form"] is True
        assert all("label" in o for o in ask.data["options"])

    @pytest.mark.asyncio
    async def test_decisive_asks_only_safety_critical(self) -> None:
        # Vague scope (class B) is NOT asked at decisive; safety (D) is.
        backend = ScriptedBackend([ScriptedRound(text="x"), ScriptedRound(text="y")])
        loop, _ = _make_loop(backend, persona=_persona("decisive"))

        _c1, e_vague = await _collect(loop, _conv(), "draft a complaint")
        assert not [e for e in e_vague if e.type == "asking_user"]

        _c2, e_safety = await _collect(loop, _conv(), "delete everything")
        assert [e for e in e_safety if e.type == "asking_user"]


class TestClearMessageUnchanged:
    @pytest.mark.asyncio
    async def test_clear_message_generates_normally(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="Here is the answer.")])
        loop, stores = _make_loop(backend, persona=_persona("cautious"))
        conv = _conv()

        chunks, events = await _collect(loop, conv, "What time is the hearing tomorrow?")

        assert not [e for e in events if e.type == "asking_user"]
        assert backend.chat_stream_calls == 1
        assert "Here is the answer." in "".join(c.delta for c in chunks)
        assert len(stores["episodic"].writes) == 1  # normal write-back


class TestSuppressionInjectsAssumption:
    @pytest.mark.asyncio
    async def test_balanced_suppresses_vague_scope_with_assumption_nudge(self) -> None:
        backend = _CapturingBackend([ScriptedRound(text="Assuming X, here goes.")])
        loop, _ = _make_loop(backend, persona=_persona("balanced"))

        _chunks, events = await _collect(loop, _conv(), "draft a complaint")

        # Balanced does not ask on vague scope (class B) → no question, generates.
        assert not [e for e in events if e.type == "asking_user"]
        assert backend.chat_stream_calls == 1
        nudge = [
            m for m in backend.last_messages if m.role == "system" and "assumption" in m.content
        ]
        assert nudge, "expected a stated-assumption system nudge in the prompt"


class TestSuppressors:
    @pytest.mark.asyncio
    async def test_answer_turn_after_question_not_re_detected(self) -> None:
        # Last turn was a proactive question → this message is its answer.
        prior = [
            ConversationMessage(
                role="assistant",
                content="This could permanently affect target. How should I proceed?",
                created_at=datetime.now(UTC),
                metadata={"proactive_question": "true"},
            ),
        ]
        backend = ScriptedBackend([ScriptedRound(text="ok")])
        loop, _ = _make_loop(backend, persona=_persona("cautious"))

        _chunks, events = await _collect(loop, _conv(prior), "delete everything")

        assert not [e for e in events if e.type == "asking_user"]
        assert backend.chat_stream_calls == 1

    @pytest.mark.asyncio
    async def test_equivalent_question_is_deduped(self) -> None:
        # An equivalent question was already asked earlier this conversation
        # (not the immediately-previous turn) → do not re-ask (D-21-6).
        prior = [
            ConversationMessage(
                role="assistant",
                content="This could permanently affect target. How should I proceed?",
                created_at=datetime.now(UTC),
                metadata={"proactive_question": "true"},
            ),
            ConversationMessage(
                role="user", content="never mind that", created_at=datetime.now(UTC)
            ),
        ]
        backend = ScriptedBackend([ScriptedRound(text="ok")])
        loop, _ = _make_loop(backend, persona=_persona("cautious"))

        _chunks, events = await _collect(loop, _conv(prior), "delete everything")

        assert not [e for e in events if e.type == "asking_user"]
        assert backend.chat_stream_calls == 1
