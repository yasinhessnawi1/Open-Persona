"""T21 (§2.9 RISKY half) — refusal auto-retry guardrail, DEFAULT-OFF.

Exercises the turn-loop one-shot corrective retry: when armed via
``PERSONA_REFUSAL_RETRY_ENABLED`` AND the model produced no tool call AND its
text refused an available tool, the loop injects ONE corrective system message
and re-generates exactly once. Pins: off-by-default (no retry, refusal text
stands), on→exactly-one-retry (corrected text + ``refusal_retry_engaged``),
and the no-false-positive case (no refusal → no retry even when armed).
"""

# ruff: noqa: SLF001, ARG001
from __future__ import annotations

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

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]
_REFUSAL = "I'm sorry, but I can't generate images — I'm only a text-based assistant."
_CORRECTED = "Sure — generating that image now."


@tool(name="generate_image", description="YOU CAN generate images. Use this tool.")
async def _fake_generate_image(prompt: str) -> ToolResult:
    return ToolResult(tool_name="generate_image", content="ok", is_error=False)


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid", role="assistant", background="b", constraints=["c"]
        ),
    )


def _make_loop(backend: ScriptedBackend) -> tuple[ConversationLoop, MemoryTurnLogWriter]:
    toolbox = Toolbox([_fake_generate_image], allow_list=None)  # type: ignore[arg-type]
    registry = TierRegistry({"mid": TierConfig(name="mid", backend_config=_DUMMY_CFG)})
    registry._cache = {"mid": backend}  # type: ignore[assignment]
    writer = MemoryTurnLogWriter()
    loop = ConversationLoop(
        persona=_persona(),
        stores={k: FakeStore() for k in ("identity", "self_facts", "worldview", "episodic")},  # type: ignore[arg-type]
        toolbox=toolbox,
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


@pytest.mark.asyncio
async def test_default_off_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSONA_REFUSAL_RETRY_ENABLED", raising=False)
    backend = ScriptedBackend([ScriptedRound(text=_REFUSAL), ScriptedRound(text=_CORRECTED)])
    loop, writer = _make_loop(backend)
    chunks = [c async for c in loop.turn(_conv(), "make me an image")]
    text = "".join(c.delta for c in chunks)
    assert _REFUSAL in text
    assert _CORRECTED not in text  # second round NOT consumed
    assert writer.logs[-1].refusal_retry_engaged is False
    # Observability still records the refusal (T11/T12 path).
    assert "generate_image" in writer.logs[-1].tool_refusal_detected


@pytest.mark.asyncio
async def test_armed_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_REFUSAL_RETRY_ENABLED", "true")
    backend = ScriptedBackend([ScriptedRound(text=_REFUSAL), ScriptedRound(text=_CORRECTED)])
    loop, writer = _make_loop(backend)
    chunks = [c async for c in loop.turn(_conv(), "make me an image")]
    text = "".join(c.delta for c in chunks)
    assert _CORRECTED in text  # retry generation consumed + streamed
    assert writer.logs[-1].refusal_retry_engaged is True


@pytest.mark.asyncio
async def test_armed_but_no_refusal_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_REFUSAL_RETRY_ENABLED", "1")
    backend = ScriptedBackend(
        [ScriptedRound(text="Here is your answer."), ScriptedRound(text=_CORRECTED)]
    )
    loop, writer = _make_loop(backend)
    chunks = [c async for c in loop.turn(_conv(), "hi")]
    text = "".join(c.delta for c in chunks)
    assert "Here is your answer." in text
    assert _CORRECTED not in text  # no second round
    assert writer.logs[-1].refusal_retry_engaged is False


@pytest.mark.asyncio
async def test_armed_retry_only_once_not_looping(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both rounds refuse — the guardrail must still fire exactly ONCE (no loop).
    monkeypatch.setenv("PERSONA_REFUSAL_RETRY_ENABLED", "yes")
    backend = ScriptedBackend([ScriptedRound(text=_REFUSAL), ScriptedRound(text=_REFUSAL)])
    loop, writer = _make_loop(backend)
    _ = [c async for c in loop.turn(_conv(), "make me an image")]
    assert writer.logs[-1].refusal_retry_engaged is True
    assert backend._index == 2  # exactly two generations: original + one retry
