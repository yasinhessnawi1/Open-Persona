"""Unit tests for the Spec 18 UnifiedRouter (T09–T11).

Covers Layer 1 invocation, Layer 2 scoring + best-tier selection,
partial-metadata fallback (D-18-X-partial-metadata-behaviour option (a)),
scoring-error fallback (D-18-X-fallback-instrumentation), and bounded
fallback (D-18-4 voice 30ms / text 100ms).
"""

from __future__ import annotations

import time

import pytest
from persona.backends import BackendConfig
from persona.backends.errors import (
    NoVisionTierConfiguredError,
    RoutingConstraintsUnsatisfiableError,
)
from persona_runtime.routing import (
    HeuristicRouter,
    RoutingContext,
    RoutingDecision,
    UnifiedRouter,
)
from persona_runtime.routing import unified as unified_module
from persona_runtime.tier import TierConfig, TierMetadata, TierRegistry


def _backend_cfg(model: str = "m") -> BackendConfig:
    return BackendConfig(provider="anthropic", model=model, api_key="sk-test")


def _metadata(
    *,
    cost_in: float = 0.3,
    cost_out: float = 1.5,
    latency: float = 800.0,
    throughput: float = 60.0,
    context_window: int = 200_000,
    tool: str = "strong",
) -> TierMetadata:
    return TierMetadata(
        cost_input_per_1k_tokens=cost_in,
        cost_output_per_1k_tokens=cost_out,
        first_token_latency_ms=latency,
        throughput_tokens_per_sec=throughput,
        context_window=context_window,
        tool_strength=tool,  # type: ignore[arg-type]
    )


def _registry(*tiers: tuple[str, TierMetadata | None]) -> TierRegistry:
    return TierRegistry(
        {
            name: TierConfig(name=name, backend_config=_backend_cfg(name), metadata=md)
            for name, md in tiers
        }
    )


def _ctx(**overrides: object) -> RoutingContext:
    defaults: dict[str, object] = {
        "requires_vision": False,
        "estimated_input_tokens": 100,
        "requires_strong_tools": False,
        "is_first_turn": False,
        "is_identity_sensitive": False,
        "is_boilerplate": False,
        "conversation_phase": "middle",
        "profile": "text_default",
    }
    defaults.update(overrides)
    return RoutingContext(**defaults)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_fallback_warn_state() -> None:
    """Clear rate-limit state between tests so warnings are deterministic."""
    unified_module._last_fallback_warned.clear()  # noqa: SLF001


# ----- Layer 2 happy path --------------------------------------------------


class TestLayer2HappyPath:
    def test_selects_highest_scoring_tier(self) -> None:
        # frontier: high cost, slow → low score on text profile.
        # small: low cost, fast → high score on text profile.
        registry = _registry(
            ("frontier", _metadata(cost_in=2.0, cost_out=10.0, latency=2000.0)),
            ("small", _metadata(cost_in=0.01, cost_out=0.02, latency=100.0)),
        )
        router = UnifiedRouter(registry)
        decision = router.route(_ctx())
        assert decision.tier == "small"
        assert decision.fallback_triggered is False
        assert decision.fallback_reason is None
        assert decision.layer2_score > 0.0
        assert "layer2" in decision.rationale

    def test_resolves_model_from_registry(self) -> None:
        registry = _registry(
            ("frontier", _metadata(cost_in=2.0, cost_out=10.0, latency=2000.0)),
            ("small", _metadata(cost_in=0.01, cost_out=0.02, latency=100.0)),
        )
        router = UnifiedRouter(registry)
        decision = router.route(_ctx())
        # Model name from BackendConfig.model (which equals the tier name here).
        assert decision.model == "small"

    def test_records_candidates_considered(self) -> None:
        registry = _registry(
            ("frontier", _metadata()),
            ("mid", _metadata()),
            ("small", _metadata()),
        )
        router = UnifiedRouter(registry)
        decision = router.route(_ctx())
        assert set(decision.candidates_considered) == {"frontier", "mid", "small"}


# ----- Partial metadata behaviour -----------------------------------------


class TestPartialMetadataBehaviour:
    def test_tiers_without_metadata_excluded_from_scoring(self) -> None:
        # Only mid has metadata → mid wins; frontier/small noted in rationale.
        registry = _registry(
            ("frontier", None),
            ("mid", _metadata(cost_in=0.1, cost_out=0.5, latency=400.0)),
            ("small", None),
        )
        router = UnifiedRouter(registry)
        decision = router.route(_ctx())
        assert decision.tier == "mid"
        assert "missing_metadata" in decision.rationale
        assert "frontier" in decision.rationale
        assert "small" in decision.rationale
        # Layer 2 succeeded for mid — NOT marked as fallback.
        assert decision.fallback_triggered is False

    def test_all_tiers_without_metadata_falls_back_to_heuristic(self) -> None:
        registry = _registry(
            ("frontier", None),
            ("mid", None),
            ("small", None),
        )
        router = UnifiedRouter(registry)
        decision = router.route(_ctx(is_first_turn=True))
        # All metadata absent → fall back to HeuristicRouter.
        assert decision.fallback_triggered is True
        assert decision.fallback_reason == "empty_metadata"
        # HeuristicRouter's first-turn rule fires.
        assert decision.tier == "frontier"
        assert "fallback (empty_metadata)" in decision.rationale


# ----- Scoring-error fallback ----------------------------------------------


class TestScoringErrorFallback:
    def test_scoring_exception_triggers_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        registry = _registry(
            ("frontier", _metadata()),
            ("mid", _metadata()),
        )

        def _boom(*_args: object, **_kwargs: object) -> float:
            msg = "synthetic scoring error"
            raise RuntimeError(msg)

        monkeypatch.setattr(unified_module.scoring, "score_tier", _boom)
        router = UnifiedRouter(registry)
        decision = router.route(_ctx(is_first_turn=True))
        assert decision.fallback_triggered is True
        assert decision.fallback_reason == "scoring_error"
        # HeuristicRouter's first-turn rule produced "frontier".
        assert decision.tier == "frontier"


# ----- Bounded latency fallback (D-18-4) ----------------------------------


class TestBoundedLatencyFallback:
    def test_voice_bound_30ms(self) -> None:
        assert unified_module.PROFILE_LATENCY_BOUNDS_MS["voice"] == 30.0

    def test_text_bound_100ms(self) -> None:
        assert unified_module.PROFILE_LATENCY_BOUNDS_MS["text_default"] == 100.0

    def test_smart_path_exceeding_bound_triggers_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _registry(
            ("frontier", _metadata()),
            ("mid", _metadata()),
        )

        # Inject a slow scorer.
        def _slow_score(*_args: object, **_kwargs: object) -> float:
            time.sleep(0.150)  # 150ms — well over the 100ms text bound
            return 0.5

        monkeypatch.setattr(unified_module.scoring, "score_tier", _slow_score)
        router = UnifiedRouter(registry)
        decision = router.route(_ctx(is_first_turn=True))
        assert decision.fallback_triggered is True
        assert decision.fallback_reason == "timeout"
        assert "smart_path_ms" in decision.rationale


# ----- Layer 1 constraint failures propagate ------------------------------


class TestLayer1FailuresPropagate:
    def test_no_vision_tier_propagates_as_subclass(self) -> None:
        class _StubBackend:
            supports_vision = False
            model_name = "haiku"

        registry = TierRegistry(
            {
                "mid": TierConfig(name="mid", backend_config=_backend_cfg("haiku")),
            }
        )
        registry._cache = {"mid": _StubBackend()}  # type: ignore[assignment]  # noqa: SLF001
        router = UnifiedRouter(registry)
        with pytest.raises(NoVisionTierConfiguredError):
            router.route(_ctx(requires_vision=True))

    def test_context_window_failure_propagates(self) -> None:
        registry = _registry(("mid", _metadata(context_window=8_000)))
        router = UnifiedRouter(registry)
        with pytest.raises(RoutingConstraintsUnsatisfiableError) as excinfo:
            router.route(_ctx(estimated_input_tokens=50_000))
        assert excinfo.value.context["reason"] == "context_window_exceeded"


# ----- Fallback warning rate-limiting -------------------------------------


class TestFallbackWarningRateLimit:
    def test_warning_emitted_once_per_60s_per_key(self) -> None:
        # Call _emit_fallback_warning twice rapidly — same key.
        # First call records the timestamp; second call skips.
        # We can't observe the loguru emission directly without a sink, but
        # we can observe the rate-limit dict state.
        unified_module._emit_fallback_warning("timeout", "text_default")  # noqa: SLF001
        first_ts = unified_module._last_fallback_warned[("timeout", "text_default")]  # noqa: SLF001
        unified_module._emit_fallback_warning("timeout", "text_default")  # noqa: SLF001
        # Second call within rate-limit window — timestamp unchanged.
        assert (
            unified_module._last_fallback_warned[("timeout", "text_default")]  # noqa: SLF001
            == first_ts
        )

    def test_distinct_keys_warn_independently(self) -> None:
        unified_module._emit_fallback_warning("timeout", "text_default")  # noqa: SLF001
        unified_module._emit_fallback_warning("timeout", "voice")  # noqa: SLF001
        unified_module._emit_fallback_warning("scoring_error", "text_default")  # noqa: SLF001
        assert ("timeout", "text_default") in unified_module._last_fallback_warned  # noqa: SLF001
        assert ("timeout", "voice") in unified_module._last_fallback_warned  # noqa: SLF001
        assert (
            "scoring_error",
            "text_default",
        ) in unified_module._last_fallback_warned  # noqa: SLF001


# ----- Strangler-fig compatibility ----------------------------------------


class TestStranglerFigCompat:
    def test_unified_router_satisfies_router_protocol(self) -> None:
        from persona_runtime.routing import Router  # Protocol

        registry = _registry(("mid", _metadata()))
        router = UnifiedRouter(registry)
        assert isinstance(router, Router)

    def test_unified_router_returns_routing_decision(self) -> None:
        registry = _registry(("mid", _metadata()))
        router = UnifiedRouter(registry)
        decision = router.route(_ctx())
        assert isinstance(decision, RoutingDecision)

    def test_unified_router_uses_supplied_heuristic_fallback(self) -> None:
        registry = _registry(("mid", _metadata()))
        fallback = HeuristicRouter(tier_registry=registry)
        router = UnifiedRouter(registry, heuristic_fallback=fallback)
        assert router._fallback is fallback  # noqa: SLF001
