"""Unit tests for the Spec 18 Router Protocol + RouterScorer Protocol (T03).

Plus the constraint-failure error hierarchy: ``RoutingConstraintsUnsatisfiableError``
as the new generalised parent (D-18-X-constraint-failure-shape) with
``NoVisionTierConfiguredError`` as a back-compat subclass. The existing
Spec 13 raise site at ``router.py:202`` is verified to still produce an
instance catchable as both the specific subclass AND the generalised parent.
"""

from __future__ import annotations

import pytest
from persona.backends.errors import (
    NoVisionTierConfiguredError,
    RoutingConstraintsUnsatisfiableError,
)
from persona.errors import PersonaError
from persona_runtime.routing import (
    Router,
    RouterScorer,
    RoutingContext,
    RoutingDecision,
)

# ----- Router Protocol -----------------------------------------------------


class _MinimalRouter:
    """Minimal implementation used for Protocol-conformance testing."""

    def route(self, context: RoutingContext) -> RoutingDecision:  # noqa: ARG002 — test stub
        return RoutingDecision(
            tier="mid",
            model="claude-haiku-4-5",
            rationale="test",
            candidates_considered=("mid",),
        )


class _UnrelatedClass:
    """An unrelated class with no route() method — must NOT satisfy Router."""

    def do_something(self) -> None:
        pass


class TestRouterProtocol:
    def test_protocol_isinstance_with_implementation(self) -> None:
        impl = _MinimalRouter()
        assert isinstance(impl, Router)

    def test_protocol_isinstance_rejects_unrelated(self) -> None:
        unrelated = _UnrelatedClass()
        assert not isinstance(unrelated, Router)

    def test_route_returns_routing_decision(self) -> None:
        impl: Router = _MinimalRouter()
        ctx = RoutingContext(
            requires_vision=False,
            estimated_input_tokens=100,
            requires_strong_tools=False,
            is_first_turn=False,
            is_identity_sensitive=False,
            conversation_phase="middle",
            profile="text_default",
        )
        decision = impl.route(ctx)
        assert isinstance(decision, RoutingDecision)
        assert decision.tier == "mid"


# ----- RouterScorer Protocol -----------------------------------------------


class _MinimalScorer:
    """Minimal implementation used for Protocol-conformance testing."""

    def score(
        self,
        candidates: tuple[str, ...],
        context: RoutingContext,  # noqa: ARG002 — test stub
    ) -> dict[str, float]:
        return dict.fromkeys(candidates, 0.5)


class TestRouterScorerProtocol:
    def test_protocol_isinstance_with_implementation(self) -> None:
        impl = _MinimalScorer()
        assert isinstance(impl, RouterScorer)

    def test_protocol_isinstance_rejects_unrelated(self) -> None:
        unrelated = _UnrelatedClass()
        assert not isinstance(unrelated, RouterScorer)

    def test_score_returns_per_tier_floats(self) -> None:
        impl: RouterScorer = _MinimalScorer()
        ctx = RoutingContext(
            requires_vision=False,
            estimated_input_tokens=100,
            requires_strong_tools=False,
            is_first_turn=False,
            is_identity_sensitive=False,
            conversation_phase="middle",
            profile="text_default",
        )
        scores = impl.score(("frontier", "mid"), ctx)
        assert scores == {"frontier": 0.5, "mid": 0.5}


# ----- Error hierarchy ----------------------------------------------------


class TestRoutingConstraintsUnsatisfiableError:
    def test_inherits_from_persona_error(self) -> None:
        assert issubclass(RoutingConstraintsUnsatisfiableError, PersonaError)

    def test_construction_with_structured_context(self) -> None:
        err = RoutingConstraintsUnsatisfiableError(
            "filter set empty",
            context={
                "reason": "context_window_exceeded",
                "configured_tiers": "frontier,mid,small",
                "required": "context_window>=64000",
            },
        )
        assert err.context["reason"] == "context_window_exceeded"
        assert err.context["configured_tiers"] == "frontier,mid,small"
        assert err.context["required"] == "context_window>=64000"
        assert "context_window_exceeded" in str(err)

    def test_raise_and_catch_as_parent(self) -> None:
        with pytest.raises(RoutingConstraintsUnsatisfiableError) as excinfo:
            raise RoutingConstraintsUnsatisfiableError(
                "no strong-tools tier",
                context={
                    "reason": "no_strong_tools_tier",
                    "configured_tiers": "frontier,mid,small",
                    "required": "strong_tools",
                },
            )
        assert excinfo.value.context["reason"] == "no_strong_tools_tier"


class TestNoVisionTierConfiguredErrorSubclass:
    """The Spec 13 error is now a subclass of the Spec 18 generalised parent.

    Existing isinstance checks (at ``test_router_vision.py:179-203`` and at any
    downstream caller) keep working — that's the back-compat guarantee
    D-18-X-constraint-failure-shape locks.
    """

    def test_is_subclass_of_constraints_unsatisfiable(self) -> None:
        assert issubclass(NoVisionTierConfiguredError, RoutingConstraintsUnsatisfiableError)

    def test_still_inherits_from_persona_error(self) -> None:
        assert issubclass(NoVisionTierConfiguredError, PersonaError)

    def test_catch_as_specific_subclass(self) -> None:
        with pytest.raises(NoVisionTierConfiguredError) as excinfo:
            raise NoVisionTierConfiguredError(
                "no vision-capable tier is configured for this turn",
                context={
                    "reason": "no_vision_tier",
                    "configured_tiers": "frontier,mid,small",
                },
            )
        assert excinfo.value.context["reason"] == "no_vision_tier"

    def test_catch_as_generalised_parent(self) -> None:
        with pytest.raises(RoutingConstraintsUnsatisfiableError) as excinfo:
            raise NoVisionTierConfiguredError(
                "no vision-capable tier is configured for this turn",
                context={
                    "reason": "no_vision_tier",
                    "configured_tiers": "frontier,mid,small",
                },
            )
        # Catchable as parent; the actual instance is still the subclass.
        assert isinstance(excinfo.value, NoVisionTierConfiguredError)
        assert excinfo.value.context["reason"] == "no_vision_tier"

    def test_existing_context_shape_preserved(self) -> None:
        """Existing Spec 13 raise sites use the two-field context shape.

        The new ``required`` field is OPTIONAL on this subclass per the
        back-compat lean recorded in :class:`RoutingConstraintsUnsatisfiableError`'s
        docstring. The router.py:202 raise site continues to work with no
        amendment.
        """
        err = NoVisionTierConfiguredError(
            "no vision-capable tier is configured for this turn",
            context={
                "reason": "no_vision_tier",
                "configured_tiers": "frontier,mid,small",
            },
        )
        assert "required" not in err.context
        assert err.context["reason"] == "no_vision_tier"
