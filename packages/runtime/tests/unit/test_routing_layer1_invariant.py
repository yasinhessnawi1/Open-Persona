"""T08 — Layer 1 invariant (T-layer1-invariant; D-18-X-layer1-extraction).

**Load-bearing test.** Generalises the Spec 13 T09 structural pattern from
``test_router_vision.py::TestStructuralPreFilter``: every implementation of
:class:`Router` must invoke
:func:`persona_runtime.routing.layer1.apply_constraint_filter` on every
:meth:`route` call, BEFORE any Layer 2 logic.

The test patches the function at the :mod:`persona_runtime.routing.layer1`
module level. Both :class:`HeuristicRouter` and :class:`UnifiedRouter` look
up the function via the ``layer1`` module (not via direct binding) — so the
patch propagates. A future router implementation that bypasses Layer 1
(e.g., a "fast path" that skips constraint filtering for known-text turns)
will fail this test loud — Layer 1 IS the design.

A future architectural drift where ``HeuristicRouter`` and ``UnifiedRouter``
get their own parallel Layer 1 implementations would also fail this test:
there is literally ONE Layer 1 implementation, accessed by both routers via
the same module-level function reference.
"""

from __future__ import annotations

import pytest
from persona.backends import BackendConfig
from persona_runtime.routing import (
    HeuristicRouter,
    Router,
    RoutingContext,
    UnifiedRouter,
)
from persona_runtime.routing import layer1 as layer1_module
from persona_runtime.tier import TierConfig, TierMetadata, TierRegistry


def _backend_cfg(model: str = "m") -> BackendConfig:
    return BackendConfig(provider="anthropic", model=model, api_key="sk-test")


def _metadata() -> TierMetadata:
    return TierMetadata(
        cost_input_per_1k_tokens=0.3,
        cost_output_per_1k_tokens=1.5,
        first_token_latency_ms=800.0,
        throughput_tokens_per_sec=60.0,
        context_window=200_000,
        tool_strength="strong",
    )


def _registry() -> TierRegistry:
    return TierRegistry(
        {
            "frontier": TierConfig(
                name="frontier", backend_config=_backend_cfg("frontier"), metadata=_metadata()
            ),
            "mid": TierConfig(name="mid", backend_config=_backend_cfg("mid"), metadata=_metadata()),
            "small": TierConfig(
                name="small", backend_config=_backend_cfg("small"), metadata=_metadata()
            ),
        }
    )


def _context() -> RoutingContext:
    return RoutingContext(
        requires_vision=False,
        estimated_input_tokens=100,
        requires_strong_tools=False,
        is_first_turn=False,
        is_identity_sensitive=False,
        is_boilerplate=False,
        conversation_phase="middle",
        profile="text_default",
    )


@pytest.fixture
def spy_layer1(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, object]]:
    """Patch :func:`apply_constraint_filter` at the layer1 module level.

    The spy records call args and forwards to the real implementation —
    routers must still receive a valid candidate set, so the patched
    function is a transparent wrapper.
    """
    calls: list[tuple[object, object]] = []
    original = layer1_module.apply_constraint_filter

    def _spy(context: RoutingContext, tier_registry: TierRegistry | None) -> tuple[str, ...]:
        calls.append((context, tier_registry))
        return original(context, tier_registry)

    monkeypatch.setattr(layer1_module, "apply_constraint_filter", _spy)
    return calls


# ----- Routers parametrised ------------------------------------------------


def _heuristic_router() -> Router:
    return HeuristicRouter(tier_registry=_registry())


def _unified_router() -> Router:
    return UnifiedRouter(_registry())


@pytest.mark.parametrize(
    ("router_factory", "router_name"),
    [
        (_heuristic_router, "HeuristicRouter"),
        (_unified_router, "UnifiedRouter"),
    ],
)
class TestLayer1FiresForEachRouter:
    """Each :class:`Router` implementation MUST consult
    :func:`apply_constraint_filter` on every :meth:`route` call.
    """

    def test_route_invocation_calls_layer1_exactly_once(
        self,
        router_factory: object,
        router_name: str,
        spy_layer1: list[tuple[object, object]],
    ) -> None:
        router = router_factory()  # type: ignore[operator]
        ctx = _context()
        router.route(ctx)
        # Both HeuristicRouter.route() and UnifiedRouter.route() must invoke
        # the Layer 1 free function exactly once per call.
        assert len(spy_layer1) == 1, (
            f"{router_name}.route() did not call apply_constraint_filter exactly once"
        )

    def test_route_passes_context_through_to_layer1(
        self,
        router_factory: object,
        router_name: str,
        spy_layer1: list[tuple[object, object]],
    ) -> None:
        router = router_factory()  # type: ignore[operator]
        ctx = _context()
        router.route(ctx)
        recorded_ctx, _recorded_registry = spy_layer1[0]
        # The router must forward the SAME RoutingContext object — no
        # silent copies / transforms before the Layer 1 filter sees it.
        assert recorded_ctx is ctx, (
            f"{router_name}.route() did not pass the original RoutingContext to "
            f"apply_constraint_filter (got identity mismatch)."
        )
