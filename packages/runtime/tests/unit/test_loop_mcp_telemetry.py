"""MCP TurnLog telemetry + MCP-gap offer (spec 27 T11/T12).

Verifies the two runtime-only JSONL fields on ``TurnLog`` (no migration, same
discipline as the Spec-26 tool-gap fields):

* ``mcp_invocations`` — the ``mcp:<server>:<tool>`` names dispatched this turn.
* ``mcp_unavailable_requested`` — catalog MCP server names the model signalled it
  lacked; a Spec-21 ``asking_user`` consent offer is emitted on the same turn.
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
    from persona.tools.protocol import AsyncTool
    from persona_runtime.agentic.events import RunEvent

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


@tool(name="mcp:time:datetime", description="MCP time tool.")
async def _mcp_time(operation: str = "now") -> ToolResult:  # noqa: ARG001 - registration stub
    return ToolResult(tool_name="mcp:time:datetime", content="2026-06-14T00:00:00 (UTC)")


@tool(name="echo", description="Echo.")
async def _echo(message: str) -> ToolResult:  # pragma: no cover - registration only
    return ToolResult(tool_name="echo", content=message, is_error=False)


def _make_loop(
    backend: ScriptedBackend, tools: list[AsyncTool]
) -> tuple[ConversationLoop, MemoryTurnLogWriter]:
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
        toolbox=Toolbox(tools, allow_list=None),  # type: ignore[arg-type]
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
async def test_mcp_gap_detected_populates_turnlog_and_emits_offer() -> None:
    backend = ScriptedBackend([ScriptedRound(text="I can't get the weather right now.")])
    loop, writer = _make_loop(backend, [_echo])  # no mcp:weather tool available

    events = await _run(loop, "What's the weather in Oslo?")

    assert writer.logs[-1].mcp_unavailable_requested == ["weather"]
    asking = [e for e in events if e.type == "asking_user"]
    assert len(asking) == 1
    assert "weather" in asking[0].data["question"]


@pytest.mark.asyncio
async def test_mcp_invocations_recorded_on_turnlog() -> None:
    backend = ScriptedBackend(
        [
            ScriptedRound(tool_name="mcp:time:datetime", tool_args={"operation": "now"}),
            ScriptedRound(text="It is midnight UTC."),
        ]
    )
    loop, writer = _make_loop(backend, [_mcp_time])

    await _run(loop, "What time is it?")

    assert writer.logs[-1].mcp_invocations == ["mcp:time:datetime"]


@pytest.mark.asyncio
async def test_no_mcp_activity_leaves_telemetry_empty() -> None:
    backend = ScriptedBackend([ScriptedRound(text="The weather is lovely today.")])
    loop, writer = _make_loop(backend, [_echo])

    await _run(loop, "How are you?")

    assert writer.logs[-1].mcp_invocations == []
    assert writer.logs[-1].mcp_unavailable_requested == []
