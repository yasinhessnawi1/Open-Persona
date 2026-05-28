"""End-to-end agentic-loop integration tests (T07).

Wires :class:`AgenticLoop` against (almost) entirely real collaborators — a real
``Toolbox`` (with mock tools that return fixed results + one that fails), a real
``SkillScanner``/``SkillInjector`` over the bundled built-in skills, real
``PromptBuilder`` / ``Router`` / ``TierRegistry`` — to prove the composition
wiring works. The model tier is a scripted backend (no live LLM); the memory
stores are in-memory doubles. Teardown calls ``registry.aclose()`` to prove the
composition root owns the lifecycle (D-05-4), not the loop.
"""

# ruff: noqa: SLF001 — the test forces the tier-registry cache to the scripted backend.

from __future__ import annotations

from pathlib import Path

import pytest
from _fakes import FakeStore, ScriptedBackend  # type: ignore[import-not-found]
from persona.backends import BackendConfig, ChatResponse
from persona.backends.types import TokenUsage
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolCall, ToolResult
from persona.skills import SkillInjector, SkillScanner, make_use_skill_tool
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime import PromptBuilder, Router, TierConfig, TierRegistry
from persona_runtime.agentic import AgenticLoop, RunEvent, RunStatus, StepType

pytestmark = pytest.mark.integration

_BUILTIN_ROOT = Path(__import__("persona").__file__).parent / "skills" / "builtin"
_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


@tool(name="web_search", description="Search the web.")
async def _web_search(query: str) -> ToolResult:
    return ToolResult(
        tool_name="web_search",
        content=f"results for {query}",
        data={"results": ["https://lovdata.no/husleieloven"]},
        is_error=False,
    )


@tool(name="web_fetch", description="Fetch a URL.")
async def _web_fetch(url: str) -> ToolResult:
    return ToolResult(tool_name="web_fetch", content=f"contents of {url}", is_error=False)


@tool(name="file_write", description="Write a file.")
async def _file_write(path: str, content: str) -> ToolResult:
    return ToolResult(tool_name="file_write", content=f"wrote {len(content)} chars to {path}")


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
    )


def _resp(content: str = "", *, tool_calls: list[ToolCall] | None = None) -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(prompt_tokens=20, completion_tokens=8, total_tokens=28),
        model="claude-sonnet-4-6",
        provider="anthropic",
        latency_ms=1.0,
    )


def _build_loop(
    script: list[ChatResponse],
) -> tuple[AgenticLoop, dict[str, FakeStore], TierRegistry]:
    persona = _persona()
    scanner = SkillScanner([_BUILTIN_ROOT])
    scanned = scanner.scan(persona.skills, tool_allow_list=persona.tools)
    tools: list[object] = [_web_search, _web_fetch, _file_write, make_use_skill_tool(scanned)]
    toolbox = Toolbox(tools, allow_list=None)  # type: ignore[arg-type]
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    backend = ScriptedBackend([], chat_script=script)
    registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]
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
        scanned_skills=scanned,
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
    )
    return loop, stores, registry


class TestMultiToolTaskEndToEnd:
    @pytest.mark.asyncio
    async def test_search_fetch_write_completes_through_real_collaborators(self) -> None:
        # Acceptance #1, #2: the spec's flagship scenario — search → fetch → write → final.
        script = [
            _resp(
                tool_calls=[
                    ToolCall(name="web_search", args={"query": "mould tenancy law"}, call_id="c1")
                ]
            ),
            _resp(
                tool_calls=[
                    ToolCall(
                        name="web_fetch",
                        args={"url": "https://lovdata.no/husleieloven"},
                        call_id="c2",
                    )
                ]
            ),
            _resp(
                tool_calls=[
                    ToolCall(
                        name="file_write",
                        args={"path": "complaint.txt", "content": "Dear landlord..."},
                        call_id="c3",
                    )
                ]
            ),
            _resp("[FINAL] Drafted the complaint letter citing husleieloven."),
        ]
        loop, stores, registry = _build_loop(script)
        events: list[RunEvent] = []

        async def on_event(ev: RunEvent) -> None:
            events.append(ev)

        run = await loop.run(
            "search for Norwegian tenancy law on mould and draft a complaint letter",
            on_event=on_event,
        )

        assert run.status is RunStatus.COMPLETED
        assert run.output is not None
        assert "complaint" in run.output.lower()
        # The step sequence is visible in Run.steps (acceptance #2).
        step_types = [s.type for s in run.steps]
        assert step_types == [
            StepType.TOOL_CALL,
            StepType.TOOL_CALL,
            StepType.TOOL_CALL,
            StepType.FINAL,
        ]
        tool_names = [s.tool_calls[0].name for s in run.steps if s.tool_calls]
        assert tool_names == ["web_search", "web_fetch", "file_write"]

        # Episodic written with the approved skill-candidate metadata (D-06-8).
        assert len(stores["episodic"].writes) == 1
        md = stores["episodic"].writes[0][0].metadata
        assert md["source"] == "agentic_run"
        assert md["tools_used"] == "file_write,web_fetch,web_search"  # sorted
        assert md["status"] == "completed"

        # The event log has the expected shape (acceptance #9).
        event_types = [e.type for e in events]
        assert event_types[0] == "started"
        assert event_types[-1] == "finished"
        assert event_types.count("tool_calling") == 3

        # Composition root owns the lifecycle (D-05-4).
        await registry.aclose()

    @pytest.mark.asyncio
    async def test_run_is_json_serialisable(self) -> None:
        # Acceptance #10, end-to-end.
        loop, _, registry = _build_loop([_resp("[FINAL] done")])
        run = await loop.run("t")
        restored = type(run).model_validate_json(run.model_dump_json())
        assert restored == run
        await registry.aclose()
