"""Unit tests for persona_runtime.loop.ConversationLoop (T07).

Two tests here are load-bearing (per the Phase 5 directive):

- ``TestBoundaryPredictionLockstep`` cross-checks the loop's ``_will_compact``
  prediction against the real ``ConversationHistoryManager.manage()`` behaviour
  across K-1 / K / K+1. A divergence means a wasted small-tier call or a dropped
  summary — the safety net for the whole D-05-X bridge.
- ``TestEpisodicWriteBackTiming`` proves a partially-consumed turn (consumer
  stops iterating early) leaves episodic empty (acceptance #10 / D-05-12),
  verified by behaviour, not by reasoning about suspension semantics.
"""

# ruff: noqa: SLF001 — tests cross-check the loop's private _will_compact predicate.

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _fakes import FakeStore, ScriptedBackend, ScriptedRound  # type: ignore[import-not-found]
from persona.backends import BackendConfig
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation, ConversationMessage
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.skills import SkillSpec
from persona.schema.tools import ToolResult
from persona.skills import SkillInjector, SkillScanner, make_use_skill_tool
from persona.skills._tokens import count_tokens
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime.logging import MemoryTurnLogWriter
from persona_runtime.loop import ConversationLoop
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


# ----- fixtures / builders -------------------------------------------------


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="tenancy assistant",
            background="Knows husleieloven.",
            constraints=["Never give binding advice."],
        ),
    )


@tool(name="echo", description="Echo a message back.")
async def _echo_tool(message: str) -> ToolResult:
    return ToolResult(tool_name="echo", content=f"echoed: {message}", is_error=False)


def _make_loop(
    backend: ScriptedBackend,
    *,
    stores: dict[str, FakeStore] | None = None,
    scanned_skills: list[SkillSpec] | None = None,
    extra_tools: list[object] | None = None,
    max_tool_rounds: int = 5,
) -> tuple[ConversationLoop, dict[str, FakeStore], MemoryTurnLogWriter]:
    stores = stores or {
        "identity": FakeStore(),
        "self_facts": FakeStore(),
        "worldview": FakeStore(),
        "episodic": FakeStore(),
    }
    tools = list(extra_tools or [_echo_tool])
    toolbox = Toolbox(tools, allow_list=None)  # type: ignore[arg-type]
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    # Force every tier to resolve to our scripted backend.
    registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]
    writer = MemoryTurnLogWriter()
    loop = ConversationLoop(
        persona=_persona(),
        stores=stores,  # type: ignore[arg-type]
        toolbox=toolbox,
        skill_scanner=SkillScanner([]),
        skill_injector=SkillInjector(),
        scanned_skills=scanned_skills or [],
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        turn_log_writer=writer,
        max_tool_rounds=max_tool_rounds,
    )
    return loop, stores, writer


def _conv(turns: int = 0) -> Conversation:
    msgs = [
        ConversationMessage(
            role="user" if i % 2 == 0 else "assistant",
            content=f"turn {i}",
            created_at=datetime.now(UTC),
        )
        for i in range(turns)
    ]
    return Conversation(conversation_id="c1", persona_id="astrid", messages=msgs)


# ----- tests ---------------------------------------------------------------


class TestPlainTurn:
    @pytest.mark.asyncio
    async def test_yields_final_chunk_and_writes_episodic(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="Hello, I'm Astrid.")])
        loop, stores, writer = _make_loop(backend)
        conv = _conv(0)

        chunks = [c async for c in loop.turn(conv, "hi")]

        assert chunks[-1].is_final is True
        accumulated = "".join(c.delta for c in chunks)
        assert "Hello, I'm Astrid." in accumulated
        # Episodic written exactly once; conversation grew by 2 (user+assistant).
        assert len(stores["episodic"].writes) == 1
        assert conv.turn_count == 2
        assert len(writer.logs) == 1
        assert writer.logs[0].tier_used == "frontier"  # first turn -> frontier

    @pytest.mark.asyncio
    async def test_streams_text_delta_by_delta(self) -> None:
        # The model emits its reply in pieces; the loop must yield each as its own
        # chunk (acceptance §6 #2 — streams char-by-char), not collapse the whole
        # response into a single buffered chunk.
        backend = ScriptedBackend([ScriptedRound(text_deltas=["Hel", "lo, ", "Astrid", " here."])])
        loop, _stores, _writer = _make_loop(backend)

        chunks = [c async for c in loop.turn(_conv(0), "hi")]

        content = [c for c in chunks if c.delta and not c.is_final]
        assert len(content) >= 4, "expected delta-by-delta chunks, not one buffered chunk"
        assert "".join(c.delta for c in chunks) == "Hello, Astrid here."
        assert chunks[-1].is_final is True


class TestToolCallLoop:
    @pytest.mark.asyncio
    async def test_dispatches_tool_then_incorporates_result(self) -> None:
        backend = ScriptedBackend(
            [
                ScriptedRound(tool_name="echo", tool_args={"message": "ping"}),
                ScriptedRound(text="The tool said: echoed: ping"),
            ]
        )
        loop, stores, writer = _make_loop(backend)
        conv = _conv(2)  # not first turn

        chunks = [c async for c in loop.turn(conv, "use the echo tool")]
        accumulated = "".join(c.delta for c in chunks)

        assert "The tool said: echoed: ping" in accumulated
        assert chunks[-1].is_final is True
        assert writer.logs[0].tool_calls == 1
        # Two chat_stream calls: tool round + final text round.
        assert backend.chat_stream_calls == 2

    @pytest.mark.asyncio
    async def test_hallucinated_tool_name_recovers_instead_of_crashing(self) -> None:
        # Spec-11 soak finding (T03): the model emits a tool call whose name is
        # not in the allow-list (a hallucination — or an empty name from a
        # malformed call). ``toolbox.dispatch`` raises ``ToolNotAllowedError``;
        # the loop must catch it, feed an is_error result back, and let the turn
        # FINISH — not let the error escape the generator and crash the SSE
        # mid-stream ("response already started"). Mirrors the agentic loop's
        # ``_dispatch`` (one tool-failure discipline across both loops).
        backend = ScriptedBackend(
            [
                ScriptedRound(tool_name="ghost_tool", tool_args={"x": "y"}, call_id="c0"),
                ScriptedRound(text="That tool doesn't exist — here is a direct answer."),
            ]
        )
        loop, _stores, writer = _make_loop(backend)

        chunks = [c async for c in loop.turn(_conv(0), "do the thing")]  # must NOT raise

        accumulated = "".join(c.delta for c in chunks)
        assert "direct answer" in accumulated
        assert chunks[-1].is_final is True
        # the bad call was dispatched once (and recovered), then the model re-answered
        assert writer.logs[0].tool_calls == 1
        assert backend.chat_stream_calls == 2


class TestMaxToolRoundsCap:
    @pytest.mark.asyncio
    async def test_cap_fires_nudge_and_one_final_generation(self) -> None:
        # cap=2: two tool rounds get dispatched (rounds 0,1), then the third
        # generation hits the cap (rounds==2) and the nudge path runs one final
        # generation, which lands on the scripted text round.
        rounds = [
            ScriptedRound(tool_name="echo", tool_args={"message": "x"}, call_id="c0"),
            ScriptedRound(tool_name="echo", tool_args={"message": "y"}, call_id="c1"),
            ScriptedRound(tool_name="echo", tool_args={"message": "z"}, call_id="c2"),
            ScriptedRound(text="best-effort answer"),
        ]
        backend = ScriptedBackend(rounds)
        loop, stores, writer = _make_loop(backend, max_tool_rounds=2)
        conv = _conv(2)

        chunks = [c async for c in loop.turn(conv, "loop forever")]
        accumulated = "".join(c.delta for c in chunks)

        assert chunks[-1].is_final is True
        assert "best-effort answer" in accumulated
        # 2 tool rounds dispatched (the 3rd hit the cap; not dispatched).
        assert writer.logs[0].tool_calls == 2
        # One round == one re-generation (D-05-11): 2 dispatched rounds + the
        # cap-detection generation + the final tool-free generation = 4 streams.
        assert backend.chat_stream_calls == 4

    @pytest.mark.asyncio
    async def test_round_counter_counts_rounds_not_individual_calls(self) -> None:
        # Regression for the round-vs-call semantics (D-05-11): the counter must
        # increment once per round, not once per tool call. With one call per
        # round and cap=1, exactly 1 round is dispatched before the cap fires.
        backend = ScriptedBackend(
            [
                ScriptedRound(tool_name="echo", tool_args={"message": "a"}, call_id="c0"),
                ScriptedRound(tool_name="echo", tool_args={"message": "b"}, call_id="c1"),
                ScriptedRound(text="final"),
            ]
        )
        loop, _stores, writer = _make_loop(backend, max_tool_rounds=1)
        conv = _conv(2)
        _ = [c async for c in loop.turn(conv, "go")]
        # cap=1: round 0 dispatched (rounds->1); round 1 generation is at_cap ->
        # nudge + final. Exactly 1 tool call dispatched.
        assert writer.logs[0].tool_calls == 1


class TestUseSkillIntercept:
    @pytest.mark.asyncio
    async def test_use_skill_injects_and_records_skill_used(self) -> None:
        skill = SkillSpec(
            name="web_research",
            description="Research the web.",
            path=__import__("pathlib").Path("/tmp/web_research/SKILL.md"),
            content="Do careful web research, cite sources.",
            content_token_count=count_tokens("Do careful web research, cite sources."),
        )
        use_skill = make_use_skill_tool([skill])
        backend = ScriptedBackend(
            [
                ScriptedRound(tool_name="use_skill", tool_args={"skill_name": "web_research"}),
                ScriptedRound(text="Now I will research carefully."),
            ]
        )
        loop, stores, writer = _make_loop(backend, scanned_skills=[skill], extra_tools=[use_skill])
        conv = _conv(2)

        chunks = [c async for c in loop.turn(conv, "please research X")]
        accumulated = "".join(c.delta for c in chunks)

        assert chunks[-1].is_final is True
        assert "Now I will research carefully." in accumulated
        assert writer.logs[0].skill_used == "web_research"


class TestEpisodicWriteBackTiming:
    """Acceptance #10 / D-05-12: a partially-consumed turn writes NOTHING."""

    @pytest.mark.asyncio
    async def test_early_consumer_exit_skips_episodic_and_turnlog(self) -> None:
        # Two rounds so there's a tool-call chunk to consume before the end.
        backend = ScriptedBackend(
            [
                ScriptedRound(tool_name="echo", tool_args={"message": "ping"}),
                ScriptedRound(text="final text that the consumer never reaches"),
            ]
        )
        loop, stores, writer = _make_loop(backend)
        conv = _conv(2)

        # Consume only the FIRST chunk, then break (simulating the user
        # navigating away mid-stream). The async generator suspends; the
        # post-loop write-back never runs.
        gen = loop.turn(conv, "start")
        first = await gen.__anext__()
        await gen.aclose()  # close the suspended generator

        assert first is not None
        assert stores["episodic"].writes == []  # NOTHING written
        assert writer.logs == []  # no TurnLog either
        assert conv.turn_count == 2  # conversation NOT appended to

    @pytest.mark.asyncio
    async def test_fully_consumed_turn_writes_exactly_once(self) -> None:
        backend = ScriptedBackend([ScriptedRound(text="done")])
        loop, stores, writer = _make_loop(backend)
        conv = _conv(0)
        _ = [c async for c in loop.turn(conv, "go")]
        assert len(stores["episodic"].writes) == 1
        assert len(writer.logs) == 1


class TestBoundaryPredictionLockstep:
    """D-05-X safety net: loop._will_compact MUST match manage()'s real decision.

    Tested across K-1 / K / K+1 for the real compact_every/keep_recent. A
    divergence means a wasted small-tier summary call or a dropped summary.
    """

    @pytest.mark.parametrize(("compact_every", "keep_recent"), [(10, 5), (6, 2), (4, 1)])
    @pytest.mark.parametrize("delta", [-1, 0, 1, 5])
    def test_prediction_matches_manage(
        self, compact_every: int, keep_recent: int, delta: int
    ) -> None:
        backend = ScriptedBackend([ScriptedRound(text="x")])
        loop, _stores, _writer = _make_loop(backend)
        # Override the loop's manager to the parametrised config.
        manager = ConversationHistoryManager(compact_every=compact_every, keep_recent=keep_recent)
        loop._history = manager  # type: ignore[assignment]

        turns = compact_every + delta
        if turns < 0:
            return
        conv = _conv(turns)

        predicted = loop._will_compact(conv)

        # Ground truth: does manage() actually call the summariser?
        calls: list[int] = []

        def counting(_msgs: list[ConversationMessage]) -> str:
            calls.append(1)
            return "summary"

        manager.manage(conv, summariser=counting)
        actual = len(calls) == 1

        assert predicted == actual, (
            f"prediction {predicted} != actual {actual} "
            f"(compact_every={compact_every}, keep_recent={keep_recent}, turns={turns})"
        )
