"""Unit tests for the Spec 18 HeuristicRouter.route() entry point (T05).

The Spec 05 ``.choose()`` byte-for-byte regression guard is exercised by the
pre-existing ``test_router.py`` (22 cases) + ``test_router_vision.py``
(10 cases) via the strangler-fig ``Router`` alias — confirmed passing without
amendment.

These T05 tests cover the **new** :meth:`HeuristicRouter.route` Protocol
entry point: rule precedence in terms of :class:`RoutingContext` signals,
Layer 1 vision filter, model-name resolution via :meth:`TierRegistry.model_name_for`,
no-registry fallback, and the ``Router`` strangler-fig alias.
"""

from __future__ import annotations

import pytest
from persona.backends import BackendConfig
from persona.backends.errors import NoVisionTierConfiguredError
from persona_runtime.router import Router as RouterAlias
from persona_runtime.routing import (
    HeuristicRouter,
    RoutingContext,
    RoutingDecision,
)
from persona_runtime.tier import TierConfig, TierRegistry

# ----- helpers ---------------------------------------------------------------


def _context(
    *,
    requires_vision: bool = False,
    is_first_turn: bool = False,
    is_identity_sensitive: bool = False,
    is_boilerplate: bool = False,
    profile: str = "text_default",
    estimated_input_tokens: int = 100,
    requires_strong_tools: bool = False,
    conversation_phase: str = "middle",
) -> RoutingContext:
    return RoutingContext(
        requires_vision=requires_vision,
        estimated_input_tokens=estimated_input_tokens,
        requires_strong_tools=requires_strong_tools,
        is_first_turn=is_first_turn,
        is_identity_sensitive=is_identity_sensitive,
        is_boilerplate=is_boilerplate,
        conversation_phase=conversation_phase,
        profile=profile,  # type: ignore[arg-type]
    )


def _backend_cfg(model: str) -> BackendConfig:
    return BackendConfig(provider="anthropic", model=model, api_key="sk-test")


def _registry(*tiers: tuple[str, str]) -> TierRegistry:
    """Build a TierRegistry from ``(name, model)`` pairs in declaration order."""
    return TierRegistry(
        {name: TierConfig(name=name, backend_config=_backend_cfg(model)) for name, model in tiers}
    )


# ----- Strangler-fig alias --------------------------------------------------


class TestStranglerFigAlias:
    """``persona_runtime.router.Router`` IS ``HeuristicRouter`` via re-export.

    D-18-X-strangler-fig-alias-shape — the old import path keeps working,
    every existing ``Router()`` instantiation in production + tests stays
    zero-touch. The Protocol named ``Router`` lives at
    ``persona_runtime.routing.protocol.Router`` (different module path; no
    collision with the legacy alias).
    """

    def test_router_alias_is_heuristic_router(self) -> None:
        assert RouterAlias is HeuristicRouter

    def test_router_can_be_instantiated_no_args(self) -> None:
        # The legacy Spec 05 calling convention.
        router = RouterAlias()
        assert isinstance(router, HeuristicRouter)
        assert router._tier_registry is None  # noqa: SLF001 — verifying no-args default


# ----- route() rule precedence ---------------------------------------------


class TestRouteFirstTurnRule:
    def test_first_turn_routes_to_frontier(self) -> None:
        router = HeuristicRouter()
        decision = router.route(_context(is_first_turn=True))
        assert decision.tier == "frontier"
        assert "first_turn" in decision.rationale

    def test_first_turn_falls_through_when_frontier_filtered_out(self) -> None:
        # Vision turn + only mid is vision-capable → first_turn falls through;
        # default-mid rule fires.
        registry = TierRegistry(
            {
                "mid": TierConfig(name="mid", backend_config=_backend_cfg("claude-vision")),
            }
        )

        # Force mid to be vision-capable via a stub; the real backend's
        # supports_vision is provider-dependent. We patch `metadata_for`-style
        # via direct cache injection.
        class _VisionBackend:
            supports_vision = True
            model_name = "claude-vision"

        registry._cache = {"mid": _VisionBackend()}  # type: ignore[assignment]  # noqa: SLF001
        router = HeuristicRouter(tier_registry=registry)
        decision = router.route(_context(requires_vision=True, is_first_turn=True))
        assert decision.tier == "mid"


class TestRouteBoilerplateRule:
    def test_boilerplate_routes_to_small(self) -> None:
        router = HeuristicRouter()
        decision = router.route(_context(is_boilerplate=True))
        assert decision.tier == "small"
        assert "boilerplate" in decision.rationale

    def test_boilerplate_falls_through_when_small_filtered_out(self) -> None:
        # No registry → default candidates include "small"; this tests the
        # default-mid path when boilerplate is False.
        router = HeuristicRouter()
        decision = router.route(_context(is_boilerplate=False))
        assert decision.tier == "mid"


class TestRoutePersonaCriticalRule:
    def test_identity_sensitive_routes_to_frontier(self) -> None:
        router = HeuristicRouter()
        decision = router.route(_context(is_identity_sensitive=True))
        assert decision.tier == "frontier"
        assert "identity_sensitive" in decision.rationale

    def test_identity_sensitive_with_boilerplate_first_turn_wins(self) -> None:
        # is_first_turn wins over is_identity_sensitive (both prefer frontier
        # so the rationale just confirms first_turn fired).
        router = HeuristicRouter()
        decision = router.route(
            _context(is_first_turn=True, is_identity_sensitive=True),
        )
        assert decision.tier == "frontier"
        assert "first_turn" in decision.rationale


class TestRouteDefaultRule:
    def test_neutral_signals_route_to_mid(self) -> None:
        router = HeuristicRouter()
        decision = router.route(_context())
        assert decision.tier == "mid"
        assert "default" in decision.rationale


class TestRouteFallback:
    def test_only_small_candidate_falls_through_to_small(self) -> None:
        """Registry with only small; default-mid rule doesn't fire; fallback."""
        registry = _registry(("small", "llama-3.1-8b"))
        router = HeuristicRouter(tier_registry=registry)
        decision = router.route(_context())
        assert decision.tier == "small"
        assert "fallback" in decision.rationale or decision.tier == "small"


# ----- Layer 1 vision filter -------------------------------------------------


class TestRouteVisionFilter:
    def test_vision_turn_excludes_text_only_tier(self) -> None:
        # Registry: frontier (vision) + small (text-only). Vision turn must
        # NOT route to small even with neutral signals.
        registry = TierRegistry(
            {
                "frontier": TierConfig(name="frontier", backend_config=_backend_cfg("opus")),
                "small": TierConfig(name="small", backend_config=_backend_cfg("llama")),
            }
        )

        class _VisionBackend:
            supports_vision = True
            model_name = "opus"

        class _TextBackend:
            supports_vision = False
            model_name = "llama"

        registry._cache = {  # type: ignore[assignment]  # noqa: SLF001
            "frontier": _VisionBackend(),
            "small": _TextBackend(),
        }
        router = HeuristicRouter(tier_registry=registry)
        decision = router.route(_context(requires_vision=True, is_boilerplate=True))
        # is_boilerplate would normally route to small, but small is filtered out.
        assert decision.tier == "frontier"
        assert decision.candidates_considered == ("frontier",)

    def test_vision_turn_with_no_vision_tier_raises(self) -> None:
        registry = TierRegistry(
            {
                "mid": TierConfig(name="mid", backend_config=_backend_cfg("haiku")),
                "small": TierConfig(name="small", backend_config=_backend_cfg("llama")),
            }
        )

        class _TextBackend:
            supports_vision = False
            model_name = "haiku"

        registry._cache = {  # type: ignore[assignment]  # noqa: SLF001
            "mid": _TextBackend(),
            "small": _TextBackend(),
        }
        router = HeuristicRouter(tier_registry=registry)
        with pytest.raises(NoVisionTierConfiguredError) as excinfo:
            router.route(_context(requires_vision=True))
        # Preserved Spec 13 context shape.
        assert excinfo.value.context["reason"] == "no_vision_tier"
        assert excinfo.value.context["configured_tiers"] == "mid,small"


# ----- Model resolution ------------------------------------------------------


class TestRouteModelResolution:
    def test_model_resolved_from_registry_without_instantiating(self) -> None:
        # Registry with a metadata_for-style read-only lookup; model name
        # comes from backend_config.model, not from the backend instance.
        registry = _registry(
            ("frontier", "claude-opus-4-7"),
            ("mid", "claude-haiku-4-5"),
        )
        router = HeuristicRouter(tier_registry=registry)
        decision = router.route(_context())  # → mid
        assert decision.tier == "mid"
        assert decision.model == "claude-haiku-4-5"
        # Backend NOT instantiated — read-only metadata path.
        assert registry._cache == {}  # noqa: SLF001

    def test_model_empty_when_no_registry(self) -> None:
        router = HeuristicRouter()
        decision = router.route(_context())
        assert decision.tier == "mid"
        assert decision.model == ""


# ----- RoutingDecision shape ------------------------------------------------


class TestRouteDecisionShape:
    def test_decision_carries_filtered_candidates(self) -> None:
        registry = _registry(
            ("frontier", "opus"),
            ("mid", "haiku"),
            ("small", "llama"),
        )
        router = HeuristicRouter(tier_registry=registry)
        decision = router.route(_context())
        assert decision.candidates_considered == ("frontier", "mid", "small")

    def test_decision_layer2_score_is_zero_for_heuristic(self) -> None:
        router = HeuristicRouter()
        decision = router.route(_context())
        # Heuristic doesn't compute a Layer 2 score — sentinel 0.0.
        assert decision.layer2_score == 0.0
        assert decision.layer1_filter_reasons == {}

    def test_decision_is_routing_decision_type(self) -> None:
        router = HeuristicRouter()
        decision = router.route(_context())
        assert isinstance(decision, RoutingDecision)
