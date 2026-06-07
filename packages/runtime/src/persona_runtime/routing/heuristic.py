"""The Spec 05 heuristic router refactored behind the Spec 18 Router Protocol.

The Spec 05 rule-based :class:`Router` becomes :class:`HeuristicRouter` —
byte-for-byte behavioural identity preserved (D-18-X-strangler-fig-alias-shape):

* :meth:`choose` is the existing Spec 05 interface, **kept verbatim**. The
  pre-existing tests at ``packages/runtime/tests/unit/test_router.py`` and
  ``test_router_vision.py`` exercise this path; they pass unchanged.
* :meth:`route` is the **new Spec 18** Protocol entry point — takes a
  :class:`RoutingContext`, applies the same five rules from the context's
  pre-classified signals, returns a :class:`RoutingDecision`.

The persona override (Spec 05 rule 1, ``persona.routing.tier_for_generation``)
and the boilerplate classifier live on the composition root for the
:meth:`route` path — :class:`~persona_runtime.loop.ConversationLoop` (T06)
pre-classifies and short-circuits BEFORE calling :meth:`route`. The
:meth:`choose` shim retains the override + boilerplate logic in-line for the
existing Spec 05 callers.

Precedence (Spec 05 §6 + Spec 18 T05 translation):

    Spec 05 rule          | choose() arg                | route() field
    ----------------------+-----------------------------+-------------------------
    1. persona override   | persona.routing.*           | (composition root)
    2. first turn         | conversation.turn_count == 0| context.is_first_turn
    3. boilerplate        | _is_boilerplate(message)    | context.is_boilerplate
    4. persona-critical   | _is_persona_critical(...)   | context.is_identity_sensitive
    5. default            | (mid)                       | (mid)

Spec 13 (T09) vision pre-filter is preserved verbatim in :meth:`_candidate_tiers`;
the :meth:`route` path consults the same logic via :meth:`_candidates_for_context`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.backends.errors import NoVisionTierConfiguredError

from persona_runtime.routing import classifiers, layer1
from persona_runtime.routing.types import RoutingContext, RoutingDecision

if TYPE_CHECKING:
    from persona.schema.conversation import Conversation
    from persona.schema.persona import Persona

    from persona_runtime.tier import TierRegistry

__all__ = ["HeuristicRouter"]


class HeuristicRouter:
    """The Spec 05 rule-based router behind the Spec 18 Router Protocol (T05).

    :meth:`choose` is the Spec 05 interface — preserved verbatim for the
    byte-for-byte regression guard at
    ``packages/runtime/tests/unit/test_router.py`` and
    ``test_router_vision.py``.

    :meth:`route` is the Spec 18 Router Protocol entry point. The composition
    root (:class:`~persona_runtime.loop.ConversationLoop` at T06) builds a
    :class:`RoutingContext` from the persona + conversation + message, applies
    the persona override short-circuit, and invokes :meth:`route` when the
    override is not active.

    Args:
        tier_registry: Optional :class:`~persona_runtime.tier.TierRegistry`.
            When provided, :meth:`route` consults it for Layer 1 (vision
            constraint) and to resolve the chosen tier → concrete model name.
            When ``None`` (the legacy unit-test path), :meth:`route` operates
            on the canonical ``("frontier", "mid", "small")`` candidate set
            and emits ``model=""``.

    Note:
        The legacy ``Router()`` constructor (no args) keeps working via the
        strangler-fig alias at ``persona_runtime.router`` — existing tests
        that do ``router = Router()`` followed by
        ``router.choose(..., tier_registry=...)`` continue to pass unchanged.
    """

    def __init__(self, tier_registry: TierRegistry | None = None) -> None:
        self._tier_registry = tier_registry

    # ----- Spec 18 Router Protocol entry point ----------------------------

    def route(self, context: RoutingContext) -> RoutingDecision:
        """Return a :class:`RoutingDecision` for the turn described by ``context``.

        Applies the Spec 05 rule precedence (rules 2–5; rule 1 — persona
        override — is the composition root's responsibility) over the
        Layer 1-filtered candidate set:

        1. ``context.is_first_turn`` and ``"frontier"`` in candidates →
           ``"frontier"``.
        2. ``context.is_boilerplate`` and ``"small"`` in candidates →
           ``"small"``.
        3. ``context.is_identity_sensitive`` and ``"frontier"`` in
           candidates → ``"frontier"``.
        4. ``"mid"`` in candidates → ``"mid"``.
        5. Fallback → the first configured candidate.

        Args:
            context: The turn's routing context.

        Returns:
            A :class:`RoutingDecision` with ``layer2_score=0.0`` (heuristic
            does not compute a score; the rationale names the firing rule)
            and ``layer1_filter_reasons={}`` (heuristic does not track Layer 1
            reasons — :class:`UnifiedRouter` does in T09).

        Raises:
            NoVisionTierConfiguredError: ``context.requires_vision`` is
                ``True`` and no tier in the registry is vision-capable
                (preserved Spec 13 fail-loud).
        """
        candidates = self._candidates_for_context(context)

        tier, rationale = self._apply_rules(context, candidates)
        model = self._resolve_model(tier)

        return RoutingDecision(
            tier=tier,
            model=model,
            rationale=rationale,
            candidates_considered=candidates,
            layer1_filter_reasons={},
            layer2_score=0.0,
        )

    # ----- Spec 05 back-compat shim (preserved verbatim) ------------------

    def choose(
        self,
        persona: Persona,
        message: str,
        conversation: Conversation,
        *,
        turn_has_image: bool = False,
        tier_registry: TierRegistry | None = None,
    ) -> str:
        """Return the tier name for this turn (Spec 05 shim).

        Preserved verbatim from the Spec 05 :class:`Router.choose` for the
        byte-for-byte regression guard — see the class docstring. The
        ``tier_registry`` kwarg overrides any registry passed to the
        constructor for this single call (the existing Spec 05 calling
        convention).

        See Spec 05 ``router.py`` docstring + ``spec_05_runtime.md`` §6 for
        the rule precedence rationale.
        """
        candidates = self._candidate_tiers(
            turn_has_image=turn_has_image, tier_registry=tier_registry
        )

        override = persona.routing.tier_for_generation
        if override != "auto" and override in candidates:
            return override

        if conversation.turn_count == 0 and "frontier" in candidates:
            return "frontier"

        if self._is_boilerplate(message) and "small" in candidates:
            return "small"

        if self._is_persona_critical(message, persona) and "frontier" in candidates:
            return "frontier"

        if "mid" in candidates:
            return "mid"

        # No rule's preferred tier is available in the candidate set. Fall
        # back to the first candidate (preserves the caller's configured
        # tier order; for the image path this is the first vision-capable
        # tier).
        return candidates[0]

    # ----- Internals — Spec 05 helpers preserved verbatim -----------------

    def _candidate_tiers(
        self,
        *,
        turn_has_image: bool,
        tier_registry: TierRegistry | None,
    ) -> tuple[str, ...]:
        """Filtered tier names the rules may return (Spec 13 T09).

        Preserved verbatim from the Spec 05 :class:`Router._candidate_tiers`.
        The Spec 13 T09 structural test at
        ``test_router_vision.py::TestStructuralPreFilter`` patches this
        method via ``patch.object(Router, "_candidate_tiers", ...)`` — the
        strangler-fig alias keeps ``Router`` resolving to
        :class:`HeuristicRouter`, so the patch target still resolves.
        """
        if tier_registry is None:
            return ("frontier", "mid", "small")

        configured = tier_registry.configured_tier_names
        if not turn_has_image:
            return configured

        vision = tuple(name for name in configured if tier_registry.supports_vision_for(name))
        if not vision:
            raise NoVisionTierConfiguredError(
                "no vision-capable tier is configured for this turn",
                context={
                    "reason": "no_vision_tier",
                    "configured_tiers": ",".join(configured),
                },
            )
        return vision

    def _is_boilerplate(self, message: str) -> bool:
        """Delegate to :func:`classifiers.is_boilerplate` (preserved verbatim)."""
        return classifiers.is_boilerplate(message)

    def _is_persona_critical(self, message: str, persona: Persona) -> bool:
        """Delegate to :func:`classifiers.is_persona_critical` (preserved verbatim)."""
        return classifiers.is_persona_critical(message, persona)

    def _persona_keywords(self, persona: Persona) -> set[str]:
        """Delegate to :func:`classifiers.persona_keywords` (preserved verbatim)."""
        return classifiers.persona_keywords(persona)

    # ----- Spec 18 route() internals --------------------------------------

    def _candidates_for_context(self, context: RoutingContext) -> tuple[str, ...]:
        """Layer 1 filter for the :meth:`route` path (Spec 18 T09).

        Delegates to :func:`persona_runtime.routing.layer1.apply_constraint_filter`
        via the module — both :class:`HeuristicRouter` and
        :class:`UnifiedRouter` look it up at call time so T08's invariant
        test patches the module-level binding and verifies both routers
        honour the patch (D-18-X-layer1-extraction).
        """
        return layer1.apply_constraint_filter(context, self._tier_registry)

    def _apply_rules(
        self,
        context: RoutingContext,
        candidates: tuple[str, ...],
    ) -> tuple[str, str]:
        """Apply the Spec 05 rules 2–5 to ``context`` over ``candidates``.

        Returns ``(tier, rationale)``. The rationale names the firing rule
        for the TurnLog and the run-viewer surface.
        """
        if context.is_first_turn and "frontier" in candidates:
            return "frontier", "first_turn → frontier"
        if context.is_boilerplate and "small" in candidates:
            return "small", "boilerplate → small"
        if context.is_identity_sensitive and "frontier" in candidates:
            return "frontier", "identity_sensitive → frontier"
        if "mid" in candidates:
            return "mid", "default → mid"
        # Fallback — first configured candidate (preserves vision-filtered order).
        return candidates[0], f"fallback → {candidates[0]}"

    def _resolve_model(self, tier: str) -> str:
        """Return the concrete model name from the registry, or ``""`` when absent."""
        if self._tier_registry is None:
            return ""
        return self._tier_registry.model_name_for(tier)
