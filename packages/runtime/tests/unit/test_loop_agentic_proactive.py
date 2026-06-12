"""Agentic-loop proactive question tests — spec 21 T07.

Verifies the additive 3+1 options on ``[ASK_USER]`` (D-21-9), the autonomy-scaled
per-run cap (D-21-5), per-run dedup (D-21-6), and that a question always consumes
a step even when suppressed (D-21-15). The model-initiated marker path is
otherwise unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _fakes import FakeStore, ScriptedBackend  # type: ignore[import-not-found]
from persona.backends import BackendConfig, ChatResponse
from persona.backends.types import TokenUsage
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolResult
from persona.skills import SkillInjector
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime.agentic.loop import AgenticLoop
from persona_runtime.agentic.step import StepType
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry

if TYPE_CHECKING:
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


def _resp(content: str = "") -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=[],
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="claude-sonnet-4-6",
        provider="anthropic",
        latency_ms=1.0,
    )


def _make_loop(
    script: list[ChatResponse], *, persona: Persona | None = None, max_steps: int = 20
) -> AgenticLoop:
    stores: dict[str, FakeStore] = {
        "identity": FakeStore(),
        "self_facts": FakeStore(),
        "worldview": FakeStore(),
        "episodic": FakeStore(),
    }
    backend = ScriptedBackend([], chat_script=script)
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]  # noqa: SLF001
    return AgenticLoop(
        persona=persona or _persona(),
        stores=stores,  # type: ignore[arg-type]
        toolbox=Toolbox([_echo], allow_list=None),  # type: ignore[arg-type]
        skill_injector=SkillInjector(),
        scanned_skills=[],
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        max_steps=max_steps,
    )


async def _run(loop: AgenticLoop, task: str, **kw: object) -> tuple[object, list[RunEvent]]:
    events: list[RunEvent] = []

    async def on_event(ev: RunEvent) -> None:
        events.append(ev)

    async def respond(_q: str) -> str:
        return "Yes"

    run = await loop.run(task, on_event=on_event, user_respond=respond, **kw)  # type: ignore[arg-type]
    return run, events


class TestOptionsPayload:
    @pytest.mark.asyncio
    async def test_ask_user_event_carries_3_plus_1_options(self) -> None:
        loop = _make_loop([_resp("[ASK_USER] Which apartment?"), _resp("[FINAL] done")])
        _run_obj, events = await _run(loop, "t")
        ask = next(e for e in events if e.type == "asking_user")
        assert len(ask.data["options"]) == 3
        assert ask.data["allow_free_form"] is True
        assert ask.data["question"] == "Which apartment?"


class TestPerRunCap:
    @pytest.mark.asyncio
    async def test_decisive_caps_at_one_question(self) -> None:
        # decisive → cap 1: the second question is suppressed (no event), but
        # still recorded as an ASK_USER step (D-21-15).
        script = [
            _resp("[ASK_USER] First question?"),
            _resp("[ASK_USER] Second different question?"),
            _resp("[FINAL] done"),
        ]
        loop = _make_loop(script, persona=_persona("decisive"))
        run, events = await _run(loop, "t")

        asking = [e for e in events if e.type == "asking_user"]
        assert len(asking) == 1  # cap 1
        ask_steps = [s for s in run.steps if s.type is StepType.ASK_USER]  # type: ignore[attr-defined]
        assert len(ask_steps) == 2  # both consume a step
        assert ask_steps[1].user_answer is None  # suppressed → proceeded

    @pytest.mark.asyncio
    async def test_cautious_allows_multiple_distinct_questions(self) -> None:
        script = [
            _resp("[ASK_USER] First question?"),
            _resp("[ASK_USER] Second different question?"),
            _resp("[FINAL] done"),
        ]
        loop = _make_loop(script, persona=_persona("cautious"))  # cap 5
        _run_obj, events = await _run(loop, "t")
        assert len([e for e in events if e.type == "asking_user"]) == 2


class TestDedup:
    @pytest.mark.asyncio
    async def test_repeated_question_is_deduped_within_run(self) -> None:
        script = [
            _resp("[ASK_USER] Same question?"),
            _resp("[ASK_USER] Same question?"),
            _resp("[FINAL] done"),
        ]
        loop = _make_loop(script, persona=_persona("cautious"))
        run, events = await _run(loop, "t")
        assert len([e for e in events if e.type == "asking_user"]) == 1  # deduped
        ask_steps = [s for s in run.steps if s.type is StepType.ASK_USER]  # type: ignore[attr-defined]
        assert len(ask_steps) == 2  # both still consume a step
