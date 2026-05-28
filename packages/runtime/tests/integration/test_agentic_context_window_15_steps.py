"""Acceptance #11 (integration): a 15-step run with large tool results stays
within the frontier context window via the step-history compactor.

Drives a real :class:`AgenticLoop` + real :class:`StepHistoryCompactor` through a
run where each step's tool returns a 2000-char result, and asserts the working
context never exceeds the frontier 200K-token window — both in the natural case
(15 * 2000 chars fits outright) and when the budget is small enough to force the
compaction path the spec relies on.
"""

# ruff: noqa: SLF001 — the test forces the tier-registry cache to a scripted backend.

from __future__ import annotations

import pytest
from _fakes import FakeStore, ScriptedBackend  # type: ignore[import-not-found]
from persona.backends import BackendConfig, ChatResponse
from persona.backends.types import TokenUsage
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolCall, ToolResult
from persona.skills import SkillInjector, count_tokens
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime import PromptBuilder, Router, TierConfig, TierRegistry
from persona_runtime.agentic import AgenticLoop, RunStatus
from persona_runtime.agentic.compactor import StepHistoryCompactor

pytestmark = pytest.mark.integration

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]
_BIG = "x" * 2000


@tool(name="fetch", description="Fetch a big document.")
async def _fetch(url: str) -> ToolResult:
    return ToolResult(tool_name="fetch", content=f"{url}: {_BIG}", is_error=False)


def _resp(content: str = "", *, tool_calls: list[ToolCall] | None = None) -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(prompt_tokens=20, completion_tokens=8, total_tokens=28),
        model="m",
        provider="anthropic",
        latency_ms=1.0,
    )


class _BudgetedBackend(ScriptedBackend):
    """A scripted backend that advertises a small max_tokens to force compaction."""

    def __init__(self, script: list[ChatResponse], *, max_tokens: int) -> None:
        super().__init__([], chat_script=script)
        self.max_tokens = max_tokens


def _build_loop(backend: ScriptedBackend) -> tuple[AgenticLoop, TierRegistry]:
    persona = Persona(
        persona_id="astrid",
        identity=PersonaIdentity(name="Astrid", role="researcher", background="b", constraints=[]),
    )
    toolbox = Toolbox([_fetch], allow_list=None)  # type: ignore[arg-type]
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    # The summariser runs on the `small` tier — in production a DISTINCT backend
    # from the step tiers, so its calls don't consume the step script. A plain
    # ScriptedBackend (no chat_script) returns the fixed "SUMMARY" response.
    summariser = ScriptedBackend([])
    registry._cache = {"frontier": backend, "mid": backend, "small": summariser}  # type: ignore[assignment]
    stores: dict[str, FakeStore] = {
        "identity": FakeStore(),
        "self_facts": FakeStore(),
        "worldview": FakeStore(),
        "episodic": FakeStore(),
    }
    loop = AgenticLoop(
        persona=persona,
        stores=stores,  # type: ignore[arg-type]
        toolbox=toolbox,
        skill_injector=SkillInjector(),
        scanned_skills=[],
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        compactor=StepHistoryCompactor(),
        max_steps=20,
    )
    return loop, registry


def _fetch_then_final(n: int) -> list[ChatResponse]:
    script = [
        _resp(tool_calls=[ToolCall(name="fetch", args={"url": f"u{i}"}, call_id=f"c{i}")])
        for i in range(n)
    ]
    script.append(_resp("[FINAL] synthesised the sources"))
    return script


class TestAcceptance11:
    @pytest.mark.asyncio
    async def test_15_steps_complete_under_frontier_window(self) -> None:
        # 15 fetch steps + a final; with the default 4096 max_tokens the compactor
        # fires and keeps the context bounded. The run completes.
        backend = _BudgetedBackend(_fetch_then_final(15), max_tokens=4096)
        loop, registry = _build_loop(backend)
        run = await loop.run("research 15 sources and synthesise")
        assert run.status is RunStatus.COMPLETED
        assert len(run.steps) == 16  # 15 tool steps + 1 final
        await registry.aclose()

    @pytest.mark.asyncio
    async def test_compactor_keeps_a_huge_run_within_frontier_window(self) -> None:
        # Sanity: even a 15-step run of 2000-char results is well under 200K tokens.
        rendered = "\n".join(f"tool: u{i}: {_BIG}" for i in range(15))
        assert count_tokens(rendered) < 200_000
