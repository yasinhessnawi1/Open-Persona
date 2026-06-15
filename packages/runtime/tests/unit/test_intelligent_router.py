"""Spec 23 T10 — IntelligentRouter + candidate_models_for + reorder_primary."""

from __future__ import annotations

import pytest
from persona.backends import BackendConfig
from persona.backends.errors import BudgetExceededError, IntelligentRoutingError
from persona.backends.model_metadata import ModelMetadata
from persona.backends.multi_model import MultiModelChatBackend
from persona.schema.persona import IntelligentRoutingConfig, RoutingBudgetConfig
from persona_runtime.routing.intelligent_router import IntelligentRouter
from persona_runtime.routing.latency import FirstTokenLatencyTracker
from persona_runtime.routing.model_selection import canonical_model_id, reorder_primary
from persona_runtime.routing.types import RoutingContext
from persona_runtime.tier import TierConfig, TierRegistry


class _StubBackend:
    """Minimal ChatBackend stand-in (only the attrs the wrapper/seam read)."""

    def __init__(self, provider: str, model: str) -> None:
        self.provider_name = provider
        self.model_name = model
        self.supports_native_tools = True
        self.supports_vision = True


def _wrapper(*pairs: tuple[str, str], tier: str = "frontier") -> MultiModelChatBackend:
    return MultiModelChatBackend(
        [_StubBackend(p, m) for p, m in pairs],  # type: ignore[list-item]
        tier_name=tier,
    )


def _registry(wrapper: MultiModelChatBackend, *, tier: str = "frontier") -> TierRegistry:
    return TierRegistry(
        {
            tier: TierConfig(
                name=tier,
                backend_config=BackendConfig(provider="anthropic", model="primary"),
                preconstructed_backend=wrapper,
            )
        }
    )


class _MapResolver:
    def __init__(self, table: dict[str, ModelMetadata]) -> None:
        self._table = table

    def resolve(self, model_id: str) -> ModelMetadata | None:
        return self._table.get(model_id)


def _md(
    *, cost: float = 0.10, quality: float = 0.80, latency: float = 300.0, vision: bool = True
) -> ModelMetadata:
    return ModelMetadata(
        cost_input_per_1k_tokens=cost,
        cost_output_per_1k_tokens=cost,
        latency_p50_ms=latency,
        quality_benchmark=quality,
        tools_supported=True,
        vision_supported=vision,
        context_length=200_000,
    )


def _ctx(**overrides: object) -> RoutingContext:
    defaults: dict[str, object] = {
        "requires_vision": False,
        "estimated_input_tokens": 1000,
        "requires_strong_tools": False,
        "is_first_turn": False,
        "is_identity_sensitive": False,
        "is_boilerplate": False,
        "conversation_phase": "middle",
        "profile": "text_default",
    }
    defaults.update(overrides)
    return RoutingContext(**defaults)  # type: ignore[arg-type]


_INTELLIGENT = IntelligentRoutingConfig(enabled=True)
_NO_BUDGET = RoutingBudgetConfig()


# ----- candidate_models_for -------------------------------------------------


class TestCandidateModelsFor:
    def test_returns_multi_model_pairs(self) -> None:
        reg = _registry(_wrapper(("anthropic", "claude-3.5-sonnet"), ("deepseek", "deepseek-chat")))
        assert reg.candidate_models_for("frontier") == (
            ("anthropic", "claude-3.5-sonnet"),
            ("deepseek", "deepseek-chat"),
        )

    def test_single_backend_tier_returns_empty(self) -> None:
        # No preconstructed wrapper → nothing to choose.
        reg = TierRegistry(
            {
                "mid": TierConfig(
                    name="mid", backend_config=BackendConfig(provider="anthropic", model="m")
                )
            }
        )
        assert reg.candidate_models_for("mid") == ()

    def test_unconfigured_tier_returns_empty(self) -> None:
        reg = _registry(_wrapper(("a", "x"), ("a", "y")), tier="frontier")
        # "small" not configured, falls back to "frontier" wrapper per get() order.
        assert reg.candidate_models_for("small") == (("a", "x"), ("a", "y"))


# ----- reorder_primary ------------------------------------------------------


class TestReorderPrimary:
    def test_moves_chosen_to_front_preserving_rest(self) -> None:
        w = _wrapper(("a", "m0"), ("a", "m1"), ("a", "m2"))
        out = reorder_primary(w, "a/m2")
        assert isinstance(out, MultiModelChatBackend)
        assert [b.model_name for b in out.backends] == ["m2", "m0", "m1"]
        assert out.tier_name == "frontier"
        # Cached wrapper NOT mutated (concurrency-safe).
        assert [b.model_name for b in w.backends] == ["m0", "m1", "m2"]

    def test_chosen_already_primary_is_noop_same_instance(self) -> None:
        w = _wrapper(("a", "m0"), ("a", "m1"))
        assert reorder_primary(w, "a/m0") is w

    def test_unknown_chosen_is_noop(self) -> None:
        w = _wrapper(("a", "m0"), ("a", "m1"))
        assert reorder_primary(w, "a/nope") is w

    def test_non_wrapper_returned_unchanged(self) -> None:
        stub = _StubBackend("a", "solo")
        assert reorder_primary(stub, "a/solo") is stub  # type: ignore[arg-type]

    def test_canonical_id_helper(self) -> None:
        assert canonical_model_id("anthropic", "claude") == "anthropic/claude"
        assert canonical_model_id("openrouter", "anthropic/claude") == "anthropic/claude"


# ----- IntelligentRouter.select_model ---------------------------------------


class TestSelectModelHappyPath:
    def test_picks_highest_scorer(self) -> None:
        reg = _registry(_wrapper(("anthropic", "good"), ("deepseek", "ok")))
        resolver = _MapResolver(
            {"anthropic/good": _md(quality=0.95), "deepseek/ok": _md(quality=0.60)}
        )
        router = IntelligentRouter(tier_registry=reg, metadata_resolver=resolver)
        sel = router.select_model("frontier", _ctx(), intelligent=_INTELLIGENT, budget=_NO_BUDGET)
        assert sel.model == "anthropic/good"
        assert sel.fallback_engaged is False
        assert set(sel.score_vector) == {"cost", "quality", "latency"}
        assert "anthropic/good" in sel.model_candidates

    def test_custom_cost_weight_picks_cheaper(self) -> None:
        reg = _registry(_wrapper(("anthropic", "pricey"), ("deepseek", "cheap")))
        resolver = _MapResolver(
            {
                "anthropic/pricey": _md(cost=2.0, quality=0.95),
                "deepseek/cheap": _md(cost=0.02, quality=0.70),
            }
        )
        cfg = IntelligentRoutingConfig(
            enabled=True,
            weights={"cost": 0.9, "quality": 0.05, "latency": 0.05},  # type: ignore[arg-type]
        )
        router = IntelligentRouter(tier_registry=reg, metadata_resolver=resolver)
        sel = router.select_model("frontier", _ctx(), intelligent=cfg, budget=_NO_BUDGET)
        assert sel.model == "deepseek/cheap"
        assert sel.weights_used["cost"] == 0.9


class TestSelectModelFallback:
    def test_not_a_multi_model_tier(self) -> None:
        reg = TierRegistry(
            {
                "mid": TierConfig(
                    name="mid", backend_config=BackendConfig(provider="anthropic", model="m")
                )
            }
        )
        router = IntelligentRouter(tier_registry=reg, metadata_resolver=_MapResolver({}))
        sel = router.select_model("mid", _ctx(), intelligent=_INTELLIGENT, budget=_NO_BUDGET)
        assert sel.fallback_engaged is True
        assert sel.fallback_reason == "not_a_multi_model_tier"

    def test_metadata_miss_degrades_to_slot0(self) -> None:
        reg = _registry(_wrapper(("anthropic", "a"), ("deepseek", "b")))
        router = IntelligentRouter(tier_registry=reg, metadata_resolver=_MapResolver({}))
        sel = router.select_model("frontier", _ctx(), intelligent=_INTELLIGENT, budget=_NO_BUDGET)
        assert sel.fallback_engaged is True
        assert sel.fallback_reason == "metadata_miss"
        assert sel.model == "anthropic/a"  # slot-0 primary

    def test_metadata_miss_raises_when_fallback_disabled(self) -> None:
        reg = _registry(_wrapper(("anthropic", "a"), ("deepseek", "b")))
        cfg = IntelligentRoutingConfig(enabled=True, fallback_to_rule_based_on_miss=False)
        router = IntelligentRouter(tier_registry=reg, metadata_resolver=_MapResolver({}))
        with pytest.raises(IntelligentRoutingError):
            router.select_model("frontier", _ctx(), intelligent=cfg, budget=_NO_BUDGET)

    def test_capability_filtered_degrades_not_raises(self) -> None:
        # Vision turn, NO candidate supports vision → degrade to slot-0.
        reg = _registry(_wrapper(("anthropic", "a"), ("deepseek", "b")))
        resolver = _MapResolver({"anthropic/a": _md(vision=False), "deepseek/b": _md(vision=False)})
        router = IntelligentRouter(tier_registry=reg, metadata_resolver=resolver)
        sel = router.select_model(
            "frontier", _ctx(requires_vision=True), intelligent=_INTELLIGENT, budget=_NO_BUDGET
        )
        assert sel.fallback_engaged is True
        assert sel.fallback_reason == "capability_filtered"


class TestSelectModelBudget:
    def test_per_turn_hard_cap_raises_when_none_fit(self) -> None:
        reg = _registry(_wrapper(("anthropic", "a"), ("deepseek", "b")))
        # Both cost 1.0/1k input + 1.0/1k output; est 1000 in + 500 out → 1.5 cents/turn.
        resolver = _MapResolver({"anthropic/a": _md(cost=1.0), "deepseek/b": _md(cost=1.0)})
        router = IntelligentRouter(tier_registry=reg, metadata_resolver=resolver)
        budget = RoutingBudgetConfig(max_cents_per_turn=0.5)
        with pytest.raises(BudgetExceededError):
            router.select_model("frontier", _ctx(), intelligent=_INTELLIGENT, budget=budget)

    def test_per_turn_cap_filters_but_passes_when_one_fits(self) -> None:
        reg = _registry(_wrapper(("anthropic", "pricey"), ("deepseek", "cheap")))
        resolver = _MapResolver(
            {"anthropic/pricey": _md(cost=1.0), "deepseek/cheap": _md(cost=0.001)}
        )
        router = IntelligentRouter(tier_registry=reg, metadata_resolver=resolver)
        budget = RoutingBudgetConfig(max_cents_per_turn=0.5)
        sel = router.select_model("frontier", _ctx(), intelligent=_INTELLIGENT, budget=budget)
        assert sel.model == "deepseek/cheap"


class TestSelectModelLatencyOverride:
    def test_live_latency_overrides_static_after_min_samples(self) -> None:
        reg = _registry(_wrapper(("anthropic", "fast_static"), ("deepseek", "slow_static")))
        resolver = _MapResolver(
            {
                "anthropic/fast_static": _md(latency=100.0),
                "deepseek/slow_static": _md(latency=2000.0),
            }
        )
        tracker = FirstTokenLatencyTracker()
        # Warm the nominally-slow model to a fast observed latency (>= 5 samples).
        for _ in range(5):
            tracker.record("slow_static", 10.0)
        # Leave fast_static with 0 samples → its static 100ms holds.
        cfg = IntelligentRoutingConfig(
            enabled=True,
            weights={"cost": 0.0, "quality": 0.0, "latency": 1.0},  # type: ignore[arg-type]
        )
        router = IntelligentRouter(
            tier_registry=reg, metadata_resolver=resolver, latency_tracker=tracker
        )
        sel = router.select_model("frontier", _ctx(), intelligent=cfg, budget=_NO_BUDGET)
        # slow_static observed 10ms beats fast_static static 100ms.
        assert sel.model == "deepseek/slow_static"


class TestSelectModelCandidateFilter:
    """Spec V5 D-V5-2 — the additive hard pre-gate (the voice TTFT gate hook)."""

    def _router(self) -> IntelligentRouter:
        reg = _registry(_wrapper(("anthropic", "fast_hi_q"), ("deepseek", "slow_hi_q")))
        resolver = _MapResolver(
            {
                "anthropic/fast_hi_q": _md(latency=300.0, quality=0.80),
                "deepseek/slow_hi_q": _md(latency=2000.0, quality=0.95),
            }
        )
        return IntelligentRouter(tier_registry=reg, metadata_resolver=resolver)

    def test_default_none_is_byte_identical(self) -> None:
        # No filter: pure quality weight picks the higher-quality (slow) model.
        cfg = IntelligentRoutingConfig(
            enabled=True,
            weights={"cost": 0.0, "quality": 1.0, "latency": 0.0},  # type: ignore[arg-type]
        )
        sel = self._router().select_model("frontier", _ctx(), intelligent=cfg, budget=_NO_BUDGET)
        assert sel.model == "deepseek/slow_hi_q"
        assert sel.fallback_engaged is False

    def test_gate_excludes_slow_model_then_scores_survivors(self) -> None:
        # Same quality-only weights, but the gate excludes the slow high-quality
        # model → the fast one wins despite lower quality (gate-then-score).
        cfg = IntelligentRoutingConfig(
            enabled=True,
            weights={"cost": 0.0, "quality": 1.0, "latency": 0.0},  # type: ignore[arg-type]
        )
        sel = self._router().select_model(
            "frontier",
            _ctx(),
            intelligent=cfg,
            budget=_NO_BUDGET,
            candidate_filter=lambda _cid, md: md.latency_p50_ms <= 600.0,
        )
        assert sel.model == "anthropic/fast_hi_q"
        assert sel.fallback_engaged is False

    def test_gate_emptying_set_degrades_latency_gated(self) -> None:
        sel = self._router().select_model(
            "frontier",
            _ctx(),
            intelligent=_INTELLIGENT,
            budget=_NO_BUDGET,
            candidate_filter=lambda _cid, _md: False,  # gate everything out
        )
        assert sel.fallback_engaged is True
        assert sel.fallback_reason == "latency_gated"
        assert sel.model == "anthropic/fast_hi_q"  # rule-based slot-0
