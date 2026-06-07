"""Spec 18 routing Protocols (T03; D-18-X-protocol-location).

The :class:`Router` Protocol is the routing seam. Two implementations behind
it:

* :class:`~persona_runtime.routing.heuristic.HeuristicRouter` (T05) — the
  Spec 05 rules, byte-for-byte preserved.
* :class:`~persona_runtime.routing.unified.UnifiedRouter` (T09–T11) — Layer 1
  capability hard filter + Layer 2 sweet-spot scorer.

The :class:`RouterScorer` Protocol is the **v0.2 extras seam** for the
optional learned-router integration (D-18-1). v0.1 ships zero implementation;
the Protocol exists so that a future ``persona-runtime[learned-router]``
extras gate can plug in a RouteLLM-mf (or similar) scorer without touching
the public :class:`Router` Protocol. Internal-heuristic scorer is the v0.1
production default per R-18-1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from persona_runtime.routing.types import RoutingContext, RoutingDecision

__all__ = ["Router", "RouterScorer"]


@runtime_checkable
class Router(Protocol):
    """Pluggable routing abstraction (T03; D-18-1, D-18-X-protocol-location).

    Given the turn's :class:`~persona_runtime.routing.types.RoutingContext`,
    returns a :class:`~persona_runtime.routing.types.RoutingDecision` carrying
    the chosen tier + model + rationale + observability metadata.

    Implementations:

    * :class:`HeuristicRouter` — dependency-free, deterministic, sub-1ms.
      The fallback floor (D-18-4): always available; a routing failure on
      the smart path degrades to the heuristic, never crashes the turn.
    * :class:`UnifiedRouter` — Layer 1 hard filter + Layer 2 sweet-spot
      scorer; falls back to :class:`HeuristicRouter` on error or bound
      exceedance.

    Composed at the composition root (the API in Spec 08; integration-test
    fixtures elsewhere) — same pattern as :class:`ChatBackend` /
    :class:`MemoryStore`. The voice service (V5) composes the same Router
    with ``profile="voice"`` on the :class:`RoutingContext`.
    """

    def route(self, context: RoutingContext) -> RoutingDecision:
        """Choose a tier + model for the turn described by ``context``.

        Args:
            context: The turn's facts (vision / tokens / signals / profile).

        Returns:
            A :class:`RoutingDecision` carrying the chosen tier + model +
            rationale + Layer 1 filter reasons + Layer 2 score.

        Raises:
            RoutingConstraintsUnsatisfiableError: When Layer 1 filters the
                candidate set to empty (no configured tier can satisfy the
                context's hard requirements). Generalises the Spec 13
                :class:`NoVisionTierConfiguredError` pattern.
        """
        ...


@runtime_checkable
class RouterScorer(Protocol):
    """Optional Layer 2 scorer plug-in (T03; D-18-1 v0.2 extras seam).

    v0.1 ships ZERO implementation — the internal heuristic scorer
    (:mod:`persona_runtime.routing.scoring`, T10) is the production default
    per R-18-1's survey of 12 external routers (none cleared the bar).

    The seam exists so a future ``persona-runtime[learned-router]`` extras
    gate can plug in a RouteLLM-mf signal (or similar) without touching the
    public :class:`Router` Protocol. The 4-tier band per
    D-18-X-fallback-instrumentation (healthy / watch / alert / force-heuristic)
    governs whether an opt-in scorer remains active in production.

    See :doc:`research.md` §R-18-1 for the rationale and the gating criteria
    a candidate must clear before v0.2 adoption.
    """

    def score(
        self,
        candidates: tuple[str, ...],
        context: RoutingContext,
    ) -> dict[str, float]:
        """Return a per-tier quality score in ``[0.0, 1.0]``.

        Higher is "more capable for this turn". The :class:`UnifiedRouter`
        Layer 2 (T10) combines this score with cost + latency per the
        :data:`RoutingProfile` weights (D-18-2). A scorer that raises is
        absorbed by the fallback path (D-18-4 / D-18-X-fallback-instrumentation,
        T11) — the turn proceeds via :class:`HeuristicRouter`.

        Args:
            candidates: The Layer 1-filtered tier set (the only tiers worth
                scoring). Ordered per :class:`TierRegistry`.
            context: The turn's routing context.

        Returns:
            Mapping of tier name → quality score. Missing tiers are treated
            as un-scored and excluded from Layer 2's choice (option-(a) per
            D-18-X-partial-metadata-behaviour generalised to scorer output).
        """
        ...
