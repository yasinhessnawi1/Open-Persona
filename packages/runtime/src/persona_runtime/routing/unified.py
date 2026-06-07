"""Spec 18 UnifiedRouter — Layer 1 + Layer 2 with bounded fallback (T09–T11).

The Spec 18 layered router:

* **Layer 1** (T09) — capability hard filter via
  :func:`persona_runtime.routing.layer1.apply_constraint_filter`. The same
  function :class:`HeuristicRouter.route` uses (D-18-X-layer1-extraction).
  Constraint failures (vision / context-window / tool-strength) propagate
  as :class:`RoutingConstraintsUnsatisfiableError` — Layer 2 cannot rescue
  a Layer 1 failure (D-18-2 design intent).
* **Layer 2** (T10) — sweet-spot scorer via
  :func:`persona_runtime.routing.scoring.score_tier`. Tiers without
  metadata are excluded from scoring (D-18-X-partial-metadata-behaviour
  option (a)). When the scored set is empty, fall back to
  :class:`HeuristicRouter`.
* **Bounded fallback** (T11; D-18-4) — the whole route() call is bounded
  by a per-profile latency budget (voice 30ms / text 100ms). Any error
  during scoring → fall back. Bound exceeded → fall back. The embedded
  :class:`HeuristicRouter` is the floor that always answers (D-18-4).

Fallback observability via rate-limited :mod:`loguru.warning` per
``(reason, profile)`` per 60s (D-18-X-fallback-instrumentation); the
returned :class:`RoutingDecision` carries ``fallback_triggered`` +
``fallback_reason`` for the TurnLog extension at T12.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from persona.backends.errors import RoutingConstraintsUnsatisfiableError
from persona.logging import get_logger

from persona_runtime.routing import layer1, scoring
from persona_runtime.routing.heuristic import HeuristicRouter
from persona_runtime.routing.types import RoutingContext, RoutingDecision

if TYPE_CHECKING:
    from persona_runtime.routing.protocol import RouterScorer
    from persona_runtime.tier import TierRegistry

__all__ = ["UnifiedRouter"]

_logger = get_logger("routing.unified")

# Per-profile fallback latency bounds (D-18-4).
PROFILE_LATENCY_BOUNDS_MS: dict[str, float] = {
    "text_default": 100.0,
    "voice": 30.0,
}

# Rate-limit warning emissions per (reason, profile) key per 60s
# (D-18-X-fallback-instrumentation). Module-level state is a smell but
# justifiable for log rate-limiting — the alternative is per-call state
# that defeats the rate-limit's purpose.
_WARN_RATE_LIMIT_S = 60.0
_last_fallback_warned: dict[tuple[str, str], float] = {}


def _emit_fallback_warning(reason: str, profile: str) -> None:
    """Emit a rate-limited :mod:`loguru.warning` for a fallback event."""
    key = (reason, profile)
    now = time.monotonic()
    last = _last_fallback_warned.get(key, 0.0)
    if now - last <= _WARN_RATE_LIMIT_S:
        return
    _last_fallback_warned[key] = now
    _logger.warning(
        "unified router fallback fired reason={reason} profile={profile}",
        reason=reason,
        profile=profile,
    )


class UnifiedRouter:
    """Spec 18 layered router with bounded fallback (T09–T11).

    Composes :class:`HeuristicRouter` as the always-available fallback
    floor. The smart path (Layer 1 hard filter + Layer 2 sweet-spot scorer)
    is the production default; an error or bound-exceedance falls back to
    the heuristic. A routing failure NEVER crashes a turn — the heuristic
    answers every routable case (D-18-4).

    Args:
        tier_registry: The deployment's :class:`TierRegistry`. Required
            (unlike :class:`HeuristicRouter` which permits ``None`` for
            legacy tests).
        scorer: Optional :class:`RouterScorer` plug-in
            (D-18-1 v0.2 extras seam). v0.1 ships ``None`` — the internal
            heuristic scorer at
            :func:`persona_runtime.routing.scoring.score_tier` is the
            production default. When set, Layer 2 may consult the scorer's
            output as an additional signal in v0.2; v0.1 simply ignores it.
        heuristic_fallback: Pre-constructed :class:`HeuristicRouter`. When
            ``None``, a fresh one is built with the same registry. Inject
            an instance to share state (e.g., a per-process latency
            tracker) across routers in v0.2.
    """

    def __init__(
        self,
        tier_registry: TierRegistry,
        *,
        scorer: RouterScorer | None = None,
        heuristic_fallback: HeuristicRouter | None = None,
    ) -> None:
        self._tier_registry = tier_registry
        self._scorer = scorer
        self._fallback = heuristic_fallback or HeuristicRouter(tier_registry=tier_registry)

    def route(self, context: RoutingContext) -> RoutingDecision:
        """Return a :class:`RoutingDecision` for ``context``.

        Layered execution:

        1. Layer 1 — :func:`apply_constraint_filter` (shared with
           :class:`HeuristicRouter`). Raises propagate (correctness failure).
        2. Layer 2 — :func:`score_tier` over the filtered candidates; pick
           the highest score. Tiers without metadata excluded (partial-metadata
           option (a)). Empty scored set → fall back.
        3. Bounded — whole call wrapped in ``perf_counter()``; exceeding the
           per-profile bound (D-18-4) falls back. Any unexpected error during
           scoring falls back too. The decision carries
           ``fallback_triggered`` + ``fallback_reason`` for observability.

        Raises:
            RoutingConstraintsUnsatisfiableError: Layer 1 emptied the
                candidate set (vision / context-window / strong-tools
                constraint failure). Correctness failure — never falls back.
        """
        bound_ms = PROFILE_LATENCY_BOUNDS_MS.get(context.profile, 100.0)
        t_start = time.perf_counter()

        # Layer 1 — correctness failure propagates (never falls back).
        candidates = layer1.apply_constraint_filter(context, self._tier_registry)

        try:
            decision = self._apply_layer2(candidates, context)
        except RoutingConstraintsUnsatisfiableError:
            raise  # Layer 1's error class — should not surface here, but be defensive
        except Exception as exc:  # noqa: BLE001 — fail-safe per criterion 7
            _logger.warning(
                "unified router layer2 error; falling back error={error}",
                error=type(exc).__name__,
            )
            return self._fallback_with(context, reason="scoring_error")

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        if elapsed_ms > bound_ms:
            return self._fallback_with(
                context,
                reason="timeout",
                detail=f"smart_path_ms={elapsed_ms:.2f}; bound_ms={bound_ms:.1f}",
            )

        return decision

    # ----- Layer 2 -----------------------------------------------------------

    def _apply_layer2(
        self,
        candidates: tuple[str, ...],
        context: RoutingContext,
    ) -> RoutingDecision:
        """Score candidates; pick the best; emit fallback when scored set empty.

        Per D-18-X-partial-metadata-behaviour: tiers without metadata are
        excluded from scoring. If ALL candidates lack metadata, fall back
        to :class:`HeuristicRouter` with reason ``"empty_metadata"``. If
        SOME lack metadata, the rationale names them so operators see
        which tiers need registry population.
        """
        scores: dict[str, float] = {}
        missing_metadata: list[str] = []
        for tier in candidates:
            score = scoring.score_tier(tier, context, self._tier_registry)
            if score is None:
                missing_metadata.append(tier)
            else:
                scores[tier] = score

        if not scores:
            return self._fallback_with(context, reason="empty_metadata")

        # Preserve candidate ordering as tie-breaker — `max` picks the first
        # occurrence when keys tie; we want the configured-tier order.
        best_tier = max(scores, key=lambda t: (scores[t], -candidates.index(t)))
        best_score = scores[best_tier]
        model = self._tier_registry.model_name_for(best_tier)

        rationale_parts = [f"layer2: best={best_tier} score={best_score:.3f}"]
        if missing_metadata:
            rationale_parts.append(f"missing_metadata={','.join(missing_metadata)}")
            # Per D-18-X-fallback-instrumentation, surface a fallback warning
            # per (reason, profile) so partial-metadata configuration drift
            # is observable. The decision itself is NOT marked as fallback
            # (Layer 2 succeeded for the metadata-bearing tiers) — only the
            # rationale names the missed tiers.
            for missed in missing_metadata:
                _emit_fallback_warning(f"partial_metadata:{missed}", context.profile)

        return RoutingDecision(
            tier=best_tier,
            model=model,
            rationale="; ".join(rationale_parts),
            candidates_considered=candidates,
            layer1_filter_reasons={},
            layer2_score=best_score,
            fallback_triggered=False,
            fallback_reason=None,
        )

    # ----- Fallback --------------------------------------------------------

    def _fallback_with(
        self,
        context: RoutingContext,
        *,
        reason: str,
        detail: str | None = None,
    ) -> RoutingDecision:
        """Fall back to the embedded :class:`HeuristicRouter` and decorate."""
        _emit_fallback_warning(reason, context.profile)
        heuristic_decision = self._fallback.route(context)
        rationale = f"fallback ({reason}): {heuristic_decision.rationale}"
        if detail is not None:
            rationale = f"{rationale} [{detail}]"
        return heuristic_decision.model_copy(
            update={
                "rationale": rationale,
                "fallback_triggered": True,
                "fallback_reason": reason,
            }
        )
