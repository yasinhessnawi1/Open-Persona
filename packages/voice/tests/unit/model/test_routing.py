"""Unit tests for persona_voice.model.VoiceRoutingPolicy (spec V5 T3; D-V5-2).

The voice routing policy adds a hard first-token-latency gate within the
rule-chosen tier: gate-then-score, layered on Spec 23's IntelligentRouter. It
excludes models above the TTFT ceiling before scoring, degrades to slot-0 when
the gate empties the set, and is a no-op (rule-based slot-0) when intelligent
routing is disabled for the persona.
"""

# ruff: noqa: ANN401, ARG002 — store double with intentionally loose signatures.

from __future__ import annotations

from typing import Any

import pytest
from persona.backends import BackendConfig
from persona.backends.model_metadata import ModelMetadata
from persona.backends.multi_model import MultiModelChatBackend
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation
from persona.schema.persona import (
    IntelligentRoutingConfig,
    Persona,
    PersonaIdentity,
    RoutingConfig,
)
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.routing.intelligent_router import IntelligentRouter
from persona_runtime.routing.types import RoutingContext
from persona_runtime.tier import TierConfig, TierRegistry
from persona_voice.model import VoiceRoutingPolicy, VoiceTurnContext
from persona_voice.model.routing import DEFAULT_VOICE_TTFT_GATE_MS


class _FakeStore:
    def write(self, persona_id: str, chunks: Any, **kwargs: Any) -> None:
        return None

    def query(self, persona_id: str, query: str, top_k: int, **filters: Any) -> list[Any]:
        return []

    def get_all(self, persona_id: str, *, include_superseded: bool = False) -> list[Any]:
        return []

    def delete(self, persona_id: str) -> None:
        return None


class _StubBackend:
    def __init__(self, provider: str, model: str) -> None:
        self.provider_name = provider
        self.model_name = model
        self.supports_native_tools = True
        self.supports_vision = True


class _MapResolver:
    def __init__(self, table: dict[str, ModelMetadata]) -> None:
        self._table = table

    def resolve(self, model_id: str) -> ModelMetadata | None:
        return self._table.get(model_id)


def _md(*, quality: float, latency: float) -> ModelMetadata:
    return ModelMetadata(
        cost_input_per_1k_tokens=0.1,
        cost_output_per_1k_tokens=0.1,
        latency_p50_ms=latency,
        quality_benchmark=quality,
        tools_supported=True,
        vision_supported=True,
        context_length=200_000,
    )


def _routing_context() -> RoutingContext:
    return RoutingContext(
        requires_vision=False,
        estimated_input_tokens=500,
        requires_strong_tools=False,
        is_first_turn=False,
        is_identity_sensitive=False,
        is_boilerplate=False,
        conversation_phase="middle",
        profile="text_default",
    )


def _context(*, enabled: bool) -> VoiceTurnContext:
    wrapper = MultiModelChatBackend(
        [_StubBackend("anthropic", "fast_hi_q"), _StubBackend("deepseek", "slow_hi_q")],  # type: ignore[list-item]
        tier_name="frontier",
    )
    registry = TierRegistry(
        {
            "frontier": TierConfig(
                name="frontier",
                backend_config=BackendConfig(provider="anthropic", model="primary"),
                preconstructed_backend=wrapper,
            )
        }
    )
    resolver = _MapResolver(
        {
            "anthropic/fast_hi_q": _md(quality=0.80, latency=300.0),
            "deepseek/slow_hi_q": _md(quality=0.95, latency=2000.0),
        }
    )
    intelligent_router = IntelligentRouter(tier_registry=registry, metadata_resolver=resolver)
    persona = Persona(
        persona_id="astrid",
        identity=PersonaIdentity(name="Astrid", role="assistant", background="x", constraints=[]),
        routing=RoutingConfig(
            intelligent=IntelligentRoutingConfig(
                enabled=enabled,
                weights={"cost": 0.0, "quality": 1.0, "latency": 0.0},  # type: ignore[arg-type]
            )
        ),
    )
    return VoiceTurnContext(
        persona=persona,
        stores={k: _FakeStore() for k in ("identity", "self_facts", "worldview", "episodic")},  # type: ignore[arg-type]
        conversation=Conversation(conversation_id="c1", persona_id="astrid", messages=[]),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        intelligent_router=intelligent_router,
    )


class TestConstruction:
    def test_default_gate_is_600ms(self) -> None:
        assert VoiceRoutingPolicy().ttft_gate_ms == DEFAULT_VOICE_TTFT_GATE_MS

    def test_non_positive_gate_raises(self) -> None:
        with pytest.raises(ValueError, match="ttft_gate_ms"):
            VoiceRoutingPolicy(ttft_gate_ms=0.0)


class TestGateThenScore:
    def test_gate_excludes_slow_model_despite_higher_quality(self) -> None:
        # Quality-only weights would pick slow_hi_q (0.95); the 600ms gate
        # excludes it (2000ms), so the fast model wins (best-quality-under-gate).
        policy = VoiceRoutingPolicy()
        sel = policy.select_model(
            _context(enabled=True), tier="frontier", routing_context=_routing_context()
        )
        assert sel.model == "anthropic/fast_hi_q"
        assert sel.fallback_engaged is False

    def test_gate_emptying_set_degrades_to_slot0(self) -> None:
        # An aggressively low gate excludes both models → degrade slot-0.
        policy = VoiceRoutingPolicy(ttft_gate_ms=50.0)
        sel = policy.select_model(
            _context(enabled=True), tier="frontier", routing_context=_routing_context()
        )
        assert sel.fallback_engaged is True
        assert sel.fallback_reason == "latency_gated"


class TestIntelligentDisabled:
    def test_disabled_returns_rule_based_slot0(self) -> None:
        policy = VoiceRoutingPolicy()
        sel = policy.select_model(
            _context(enabled=False), tier="frontier", routing_context=_routing_context()
        )
        assert sel.fallback_engaged is True
        assert sel.fallback_reason == "intelligent_routing_disabled"
        assert sel.model == "primary"  # tier_registry.model_name_for("frontier")
