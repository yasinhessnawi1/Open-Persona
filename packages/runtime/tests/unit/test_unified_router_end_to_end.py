"""End-to-end test: UnifiedRouter dispatched through ConversationLoop (T13).

Verifies the full Spec 18 stack composes correctly:

* :class:`UnifiedRouter` is a drop-in :class:`Router` Protocol implementation
  accepted by :class:`ConversationLoop` without code change.
* The composition-root strangler-fig affordance does NOT mutate
  :class:`UnifiedRouter` (only :class:`HeuristicRouter` is affected).
* :class:`TurnLog` records the full :class:`RoutingDecision` end-to-end —
  TurnLog's ``routing_decision`` / ``routing_latency_ms`` /
  ``routing_fallback_triggered`` populate correctly through the
  ConversationLoop call site.

Lives in ``tests/unit/`` rather than ``tests/integration/`` because the
backend is scripted (no real model) and no external resource is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _fakes import FakeStore, ScriptedBackend, ScriptedRound  # type: ignore[import-not-found]
from persona.backends import BackendConfig
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona, PersonaIdentity
from persona.skills import SkillInjector, SkillScanner
from persona.tools import Toolbox
from persona_runtime.logging import MemoryTurnLogWriter
from persona_runtime.loop import ConversationLoop
from persona_runtime.prompt import PromptBuilder
from persona_runtime.routing import UnifiedRouter
from persona_runtime.tier import TierConfig, TierMetadata, TierRegistry

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key="sk-test")


def _metadata(*, latency_ms: float, cost_in: float = 0.1, cost_out: float = 0.5) -> TierMetadata:
    return TierMetadata(
        cost_input_per_1k_tokens=cost_in,
        cost_output_per_1k_tokens=cost_out,
        first_token_latency_ms=latency_ms,
        throughput_tokens_per_sec=80.0,
        context_window=200_000,
        tool_strength="strong",
    )


def _registry_with_unified_metadata() -> tuple[TierRegistry, ScriptedBackend]:
    """Build a 3-tier registry with metadata + pre-cache the scripted backend."""
    backend = ScriptedBackend(
        [ScriptedRound(text="here is my response")],
        supports_vision=False,
    )
    registry = TierRegistry(
        {
            "frontier": TierConfig(
                name="frontier",
                backend_config=_DUMMY_CFG,
                metadata=_metadata(latency_ms=1200.0, cost_in=1.5, cost_out=7.5),
            ),
            "mid": TierConfig(
                name="mid",
                backend_config=_DUMMY_CFG,
                metadata=_metadata(latency_ms=400.0, cost_in=0.08, cost_out=0.40),
            ),
            "small": TierConfig(
                name="small",
                backend_config=_DUMMY_CFG,
                metadata=_metadata(latency_ms=100.0, cost_in=0.005, cost_out=0.008),
            ),
        }
    )
    # Force every tier to resolve to the same scripted backend so the loop
    # runs without real backend construction.
    registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]  # noqa: SLF001
    return registry, backend


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="assistant",
            background="testing the unified router",
            constraints=[],
        ),
    )


def _conversation() -> Conversation:
    return Conversation(conversation_id="c1", persona_id="astrid", messages=[])


def _build_loop(
    router: UnifiedRouter, registry: TierRegistry
) -> tuple[ConversationLoop, MemoryTurnLogWriter]:
    stores = {
        "identity": FakeStore(),
        "self_facts": FakeStore(),
        "worldview": FakeStore(),
        "episodic": FakeStore(),
    }
    writer = MemoryTurnLogWriter()
    loop = ConversationLoop(
        persona=_persona(),
        stores=stores,  # type: ignore[arg-type]
        toolbox=Toolbox([], allow_list=None),  # type: ignore[arg-type]
        skill_scanner=SkillScanner([]),
        skill_injector=SkillInjector(),
        scanned_skills=[],
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        prompt_builder=PromptBuilder(),
        router=router,
        tier_registry=registry,
        turn_log_writer=writer,
        max_tool_rounds=5,
    )
    return loop, writer


class TestUnifiedRouterEndToEnd:
    @pytest.mark.asyncio
    async def test_unified_router_dispatches_through_loop(self) -> None:
        registry, _backend = _registry_with_unified_metadata()
        router = UnifiedRouter(registry)
        loop, writer = _build_loop(router, registry)
        conv = _conversation()
        chunks = [c async for c in loop.turn(conv, "thanks for the help")]
        assert chunks[-1].is_final is True
        # TurnLog records the unified router's decision.
        assert len(writer.logs) == 1
        log = writer.logs[0]
        assert log.routing_decision is not None
        # All three tiers have metadata → Layer 2 scoring fired (not fallback).
        assert log.routing_fallback_triggered is False
        # Rationale names Layer 2 (the smart path produced this decision).
        assert "layer2" in log.routing_decision.rationale
        # Routing-decision latency recorded.
        assert log.routing_latency_ms >= 0.0
        # Layer 2 score populated (sentinel 0.0 from HeuristicRouter would not be).
        assert log.routing_decision.layer2_score > 0.0
        # Decision's tier matches the tier the loop dispatched against.
        assert log.tier_used == log.routing_decision.tier
        # Tier is one of the configured set — Layer 2 picked from the candidates.
        assert log.tier_used in {"frontier", "mid", "small"}

    @pytest.mark.asyncio
    async def test_unified_router_fallback_path_recorded_on_partial_metadata(self) -> None:
        # Build a registry where ALL metadata is absent — forces the
        # UnifiedRouter into the empty_metadata fallback path.
        backend = ScriptedBackend(
            [ScriptedRound(text="hi")],
            supports_vision=False,
        )
        registry = TierRegistry(
            {
                "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
                "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
                "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
            }
        )
        registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]  # noqa: SLF001
        router = UnifiedRouter(registry)
        loop, writer = _build_loop(router, registry)
        conv = _conversation()
        # First-turn signal → HeuristicRouter fallback picks frontier.
        chunks = [c async for c in loop.turn(conv, "hello")]
        assert chunks[-1].is_final is True
        log = writer.logs[0]
        assert log.routing_fallback_triggered is True
        assert log.routing_fallback_reason == "empty_metadata"
        # HeuristicRouter's first-turn rule fired through the fallback.
        assert log.tier_used == "frontier"
        # Make sure rationale carries the fallback context.
        assert "fallback (empty_metadata)" in log.routing_decision.rationale  # type: ignore[union-attr]


def _datetime_now() -> datetime:
    return datetime.now(UTC)
