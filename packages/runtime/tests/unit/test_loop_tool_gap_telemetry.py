"""Tool-gap + consent TurnLog telemetry (spec 26 T10/T12).

Verifies the two runtime-only JSONL fields on ``TurnLog``:

* ``tool_gap_detected`` — populated by the post-generation tool-gap detector
  when the model says it can't do something a catalog tool (NOT in the persona's
  allow-list) would enable; empty otherwise. A Spec-21 ``asking_user`` consent
  offer is emitted on the same turn.
* ``tool_consent_granted`` — carries the tools the API enabled via the consent
  flow immediately before the turn (passed through ``turn()``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _fakes import FakeStore, ScriptedBackend, ScriptedRound  # type: ignore[import-not-found]
from persona.backends import BackendConfig
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation
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
    from persona_runtime.agentic.events import RunEvent

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


@tool(name="echo", description="Echo.")
async def _echo(message: str) -> ToolResult:  # pragma: no cover - registration only
    return ToolResult(tool_name="echo", content=message, is_error=False)


def _make_loop(backend: ScriptedBackend) -> tuple[ConversationLoop, MemoryTurnLogWriter]:
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]  # noqa: SLF001
    writer = MemoryTurnLogWriter()
    loop = ConversationLoop(
        # "decisive" autonomy keeps the pre-gen proactive question from firing so
        # the turn reaches generation + the post-gen gap detector.
        persona=Persona(
            persona_id="astrid",
            identity=PersonaIdentity(name="Astrid", role="assistant", background="bg"),
            autonomy="decisive",  # type: ignore[arg-type]
        ),
        stores={
            "identity": FakeStore(),
            "self_facts": FakeStore(),
            "worldview": FakeStore(),
            "episodic": FakeStore(),
        },  # type: ignore[arg-type]
        toolbox=Toolbox([_echo], allow_list=None),  # type: ignore[arg-type]  # calculator NOT available
        skill_scanner=SkillScanner([]),
        skill_injector=SkillInjector(),
        scanned_skills=[],
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        turn_log_writer=writer,
    )
    return loop, writer


def _conv() -> Conversation:
    return Conversation(conversation_id="c1", persona_id="astrid", messages=[])


async def _run(loop: ConversationLoop, message: str, **kwargs: object) -> list[RunEvent]:
    events: list[RunEvent] = []

    async def on_event(ev: RunEvent) -> None:
        events.append(ev)

    _chunks = [c async for c in loop.turn(_conv(), message, on_event=on_event, **kwargs)]  # type: ignore[arg-type]
    return events


@pytest.mark.asyncio
async def test_tool_gap_detected_populates_turnlog_and_emits_offer() -> None:
    backend = ScriptedBackend([ScriptedRound(text="I can't calculate that precisely.")])
    loop, writer = _make_loop(backend)

    events = await _run(loop, "What is 17 times 19?")

    assert writer.logs[-1].tool_gap_detected == ["calculator"]
    # A consent offer was surfaced (Spec-21 asking_user) on the same turn.
    asking = [e for e in events if e.type == "asking_user"]
    assert len(asking) == 1
    assert "calculator" in asking[0].data["question"]


@pytest.mark.asyncio
async def test_no_gap_leaves_telemetry_empty() -> None:
    backend = ScriptedBackend([ScriptedRound(text="17 times 19 is 323.")])
    loop, writer = _make_loop(backend)

    events = await _run(loop, "What is 17 times 19?")

    assert writer.logs[-1].tool_gap_detected == []
    assert writer.logs[-1].tool_consent_granted == []
    assert [e for e in events if e.type == "asking_user"] == []


@pytest.mark.asyncio
async def test_consent_granted_recorded_on_turn() -> None:
    backend = ScriptedBackend([ScriptedRound(text="Sure — 17 times 19 is 323.")])
    loop, writer = _make_loop(backend)

    await _run(loop, "Now compute it", consent_granted_tools=["calculator"])

    assert writer.logs[-1].tool_consent_granted == ["calculator"]
