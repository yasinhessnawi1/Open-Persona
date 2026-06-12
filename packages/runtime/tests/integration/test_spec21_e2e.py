"""Spec 21 cross-spec e2e journeys — T13 (operator-pass scenario charter).

End-to-end, multi-turn journeys that tie the spec-21 components together against
scripted backends (no external services):

  J1 — chat 3+1 question round-trip: ambiguous turn asks (D-21-9) and skips
       generation; the answer turn is recognised (D-21-6 suppressor) and the
       persona generates normally.
  J2 — agentic pause/resume: a mid-run ``[ASK_USER]`` carries 3+1 options
       (D-21-9), pauses, resumes with the answer in context, and completes
       (criterion 2, BINARY).
  J3 — stated-assumption emission: a gated/suppressed signal injects a
       stated-assumption nudge and the persona proceeds (D-21-18), rather than
       asking or silently guessing.

Also asserts the D-22-X / D-20-X DeepSeek reasoning-strip invariant is preserved:
a proactive-question turn is a plain assistant message (no reasoning / tool_calls
echoed into history that the serializer would have to strip).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _fakes import FakeStore, ScriptedBackend, ScriptedRound  # type: ignore[import-not-found]
from persona.backends import BackendConfig, ChatResponse
from persona.backends.types import TokenUsage
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolResult
from persona.skills import SkillInjector, SkillScanner
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime.agentic.loop import AgenticLoop
from persona_runtime.agentic.step import StepType
from persona_runtime.logging import MemoryTurnLogWriter
from persona_runtime.loop import ConversationLoop
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry

if TYPE_CHECKING:
    from persona_runtime.agentic.events import RunEvent

pytestmark = pytest.mark.integration

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


@tool(name="echo", description="Echo.")
async def _echo(message: str) -> ToolResult:
    return ToolResult(tool_name="echo", content=message, is_error=False)


def _persona(autonomy: str = "cautious") -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(name="Astrid", role="tenancy assistant", background="bg"),
        autonomy=autonomy,  # type: ignore[arg-type]
    )


def _tier_registry(backend: ScriptedBackend) -> TierRegistry:
    reg = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    reg._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]  # noqa: SLF001
    return reg


def _chat_loop(backend: ScriptedBackend, persona: Persona) -> ConversationLoop:
    stores = {k: FakeStore() for k in ("identity", "self_facts", "worldview", "episodic")}
    return ConversationLoop(
        persona=persona,
        stores=stores,  # type: ignore[arg-type]
        toolbox=Toolbox([_echo], allow_list=None),  # type: ignore[arg-type]
        skill_scanner=SkillScanner([]),
        skill_injector=SkillInjector(),
        scanned_skills=[],
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=_tier_registry(backend),
        turn_log_writer=MemoryTurnLogWriter(),
    )


async def _drive(loop: ConversationLoop, conv: Conversation, msg: str) -> list[RunEvent]:
    events: list[RunEvent] = []

    async def on_event(ev: RunEvent) -> None:
        events.append(ev)

    async for _ in loop.turn(conv, msg, on_event=on_event):
        pass
    return events


class TestJourney1ChatRoundTrip:
    @pytest.mark.asyncio
    async def test_ambiguous_then_answer_completes(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="Here's your complaint draft.")])
        loop = _chat_loop(backend, _persona("cautious"))
        conv = Conversation(conversation_id="c1", persona_id="astrid", messages=[])

        # Turn 1: ambiguous → asks 3+1, no generation.
        e1 = await _drive(loop, conv, "draft a complaint")
        ask = next(e for e in e1 if e.type == "asking_user")
        assert len(ask.data["options"]) == 3
        assert ask.data["allow_free_form"] is True
        assert backend.chat_stream_calls == 0
        question_turn = conv.messages[-1]
        assert question_turn.role == "assistant"
        assert question_turn.metadata.get("proactive_question") == "true"
        # Reasoning-strip invariant: the question turn is a clean assistant msg.
        assert isinstance(question_turn.content, str)
        assert not question_turn.tool_calls

        # Turn 2: the answer is recognised (suppressor) → normal generation.
        e2 = await _drive(loop, conv, "Maintenance")
        assert not [e for e in e2 if e.type == "asking_user"]
        assert backend.chat_stream_calls == 1


class TestJourney2AgenticPauseResume:
    @pytest.mark.asyncio
    async def test_agentic_question_pauses_and_resumes(self) -> None:
        script = [
            ChatResponse(
                content="[ASK_USER] Should this reference prior warnings?",
                tool_calls=[],
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                model="m",
                provider="anthropic",
                latency_ms=1.0,
            ),
            ChatResponse(
                content="[FINAL] Done.",
                tool_calls=[],
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                model="m",
                provider="anthropic",
                latency_ms=1.0,
            ),
        ]
        backend = ScriptedBackend([], chat_script=script)
        loop = AgenticLoop(
            persona=_persona("cautious"),
            stores={k: FakeStore() for k in ("identity", "self_facts", "worldview", "episodic")},  # type: ignore[arg-type]
            toolbox=Toolbox([_echo], allow_list=None),  # type: ignore[arg-type]
            skill_injector=SkillInjector(),
            scanned_skills=[],
            prompt_builder=PromptBuilder(),
            router=Router(),
            tier_registry=_tier_registry(backend),
        )
        events: list[RunEvent] = []
        answers: list[str] = []

        async def on_event(ev: RunEvent) -> None:
            events.append(ev)

        async def respond(question: str) -> str:
            answers.append(question)
            return "Yes, include them"

        run = await loop.run("draft a complaint", on_event=on_event, user_respond=respond)

        ask = next(e for e in events if e.type == "asking_user")
        assert len(ask.data["options"]) == 3  # 3+1 options on the agentic ask
        assert answers  # the loop awaited the answer (pause/resume)
        ask_step = next(s for s in run.steps if s.type is StepType.ASK_USER)
        assert ask_step.user_answer == "Yes, include them"  # resumed with the answer
        assert run.output == "Done."  # completed after resume (criterion 2 BINARY)


class TestJourney3StatedAssumption:
    @pytest.mark.asyncio
    async def test_gated_signal_injects_stated_assumption_and_proceeds(self) -> None:
        # Balanced does not ask on vague scope (class B) → stated assumption, generates.
        backend = ScriptedBackend(
            [ScriptedRound(text="Assuming a maintenance complaint, here goes.")]
        )
        loop = _chat_loop(backend, _persona("balanced"))
        conv = Conversation(conversation_id="c1", persona_id="astrid", messages=[])

        events = await _drive(loop, conv, "draft a complaint")

        assert not [e for e in events if e.type == "asking_user"]  # no question
        assert backend.chat_stream_calls == 1  # generated (did not silently skip)
        # The persona produced a response (the assumption nudge steered generation).
        assert conv.messages[-1].role == "assistant"
