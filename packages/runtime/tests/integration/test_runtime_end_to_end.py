"""End-to-end runtime integration tests (T08).

Wires the runtime against (almost) entirely real collaborators — real
``Toolbox``, real ``SkillScanner``/``SkillInjector`` over the bundled built-in
skills, real ``ConversationHistoryManager`` / ``PromptBuilder`` / ``Router`` /
``TierRegistry`` — to prove the composition wiring works. The model tier is a
scripted backend (no live LLM); the memory stores are in-memory doubles (real
Chroma persistence is spec-01's tested concern, not the runtime's). This is the
"make it run end to end" interpretation per Phase 1 steer #6: the integration
suite is the only caller of ``ConversationLoop`` in spec 05.
"""

# ruff: noqa: SLF001 — the test forces tier-registry cache to the scripted backend.

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from _fakes import FakeStore, ScriptedBackend, ScriptedRound  # type: ignore[import-not-found]
from persona.backends import BackendConfig
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolResult
from persona.skills import SkillInjector, SkillScanner, make_use_skill_tool, render_skill_index
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime import (
    ConversationLoop,
    PromptBuilder,
    Router,
    TierConfig,
    TierRegistry,
)
from persona_runtime.logging import MemoryTurnLogWriter

pytestmark = pytest.mark.integration

_BUILTIN_ROOT = Path(__import__("persona").__file__).parent / "skills" / "builtin"
_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


@tool(name="echo", description="Echo a message.")
async def _echo(message: str) -> ToolResult:
    return ToolResult(tool_name="echo", content=f"echoed: {message}", is_error=False)


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="Norwegian tenancy law assistant",
            background="Knows husleieloven.",
            constraints=["Never give binding legal advice."],
        ),
        skills=["web_research", "document_drafting"],
        # `decisive` opts out of vague_scope / missing_parameter question gating
        # (only safety_critical_gap fires) — the spec 05 integration suite tests
        # skill / tool dispatch end-to-end, not the spec 21 autonomy hook, so the
        # test is insulated from the proactive-question interception that would
        # otherwise fire on prompts like "draft a complaint" under default
        # (cautious) autonomy. Spec 21 e2e lives in test_spec21_e2e.py.
        autonomy="decisive",
    )


def _stores() -> dict[str, FakeStore]:
    return {
        "identity": FakeStore(),
        "self_facts": FakeStore(),
        "worldview": FakeStore(),
        "episodic": FakeStore(),
    }


def _build_loop(
    backend: ScriptedBackend,
) -> tuple[ConversationLoop, dict[str, FakeStore], MemoryTurnLogWriter, TierRegistry]:
    persona = _persona()
    # Real scanner over the bundled built-ins.
    scanner = SkillScanner([_BUILTIN_ROOT])
    scanned = scanner.scan(persona.skills, tool_allow_list=persona.tools)
    # Real toolbox composed with the use_skill tool (D-04-10).
    tools: list[object] = [_echo, make_use_skill_tool(scanned)]
    toolbox = Toolbox(tools, allow_list=None)  # type: ignore[arg-type]
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]
    writer = MemoryTurnLogWriter()
    stores = _stores()
    loop = ConversationLoop(
        persona=persona,
        stores=stores,  # type: ignore[arg-type]
        toolbox=toolbox,
        skill_scanner=scanner,
        skill_injector=SkillInjector(),
        scanned_skills=scanned,
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        turn_log_writer=writer,
    )
    return loop, stores, writer, registry


def _conv(turns: int = 0) -> Conversation:
    from persona.schema.conversation import ConversationMessage

    msgs = [
        ConversationMessage(
            role="user" if i % 2 == 0 else "assistant",
            content=f"turn {i}",
            created_at=datetime.now(UTC),
        )
        for i in range(turns)
    ]
    return Conversation(conversation_id="c1", persona_id="astrid", messages=msgs)


class TestPlainTurnEndToEnd:
    @pytest.mark.asyncio
    async def test_plain_turn_runs_through_all_real_collaborators(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="I can help with that.")])
        loop, stores, writer, registry = _build_loop(backend)
        conv = _conv(0)

        chunks = [c async for c in loop.turn(conv, "Hello Astrid")]

        assert chunks[-1].is_final is True
        assert "I can help with that." in "".join(c.delta for c in chunks)
        assert len(stores["episodic"].writes) == 1
        assert conv.turn_count == 2
        assert len(writer.logs) == 1
        await registry.aclose()  # composition-root owns the lifecycle (D-05-4)


class TestToolTurnEndToEnd:
    @pytest.mark.asyncio
    async def test_tool_call_turn_dispatches_real_toolbox(self) -> None:
        backend = ScriptedBackend(
            [
                ScriptedRound(tool_name="echo", tool_args={"message": "ping"}),
                ScriptedRound(text="The echo tool returned ping."),
            ]
        )
        loop, stores, writer, registry = _build_loop(backend)
        conv = _conv(2)

        chunks = [c async for c in loop.turn(conv, "echo ping for me")]

        assert chunks[-1].is_final is True
        assert "The echo tool returned ping." in "".join(c.delta for c in chunks)
        assert writer.logs[0].tool_calls == 1
        await registry.aclose()


class TestUseSkillTurnEndToEnd:
    @pytest.mark.asyncio
    async def test_use_skill_injects_real_builtin_content(self) -> None:
        # Spec 24 (D-24-9): the persona still declares the pre-Spec-24
        # ``document_drafting`` name (see ``_persona`` skills) — the alias shim
        # resolves it to ``document_generation`` at scan time, so the model
        # sees + invokes the RESOLVED name in the index. Coverage relocated
        # from document_drafting onto document_generation.
        backend = ScriptedBackend(
            [
                ScriptedRound(
                    tool_name="use_skill",
                    tool_args={"skill_name": "document_generation"},
                ),
                ScriptedRound(text="Drafting per the skill."),
            ]
        )
        loop, stores, writer, registry = _build_loop(backend)
        conv = _conv(2)

        chunks = [c async for c in loop.turn(conv, "draft a complaint")]

        assert chunks[-1].is_final is True
        assert writer.logs[0].skill_used == "document_generation"
        await registry.aclose()


class TestSkillIndexComposition:
    def test_scanner_finds_both_builtins(self) -> None:
        # The deprecated ``document_drafting`` name resolves via the alias shim
        # to ``document_generation`` (D-24-9); coverage relocated.
        scanner = SkillScanner([_BUILTIN_ROOT])
        scanned = scanner.scan(["web_research", "document_drafting"])
        names = {s.name for s in scanned}
        assert names == {"web_research", "document_generation"}
        index = render_skill_index(scanned)
        assert "web_research" in index
        assert "document_generation" in index
