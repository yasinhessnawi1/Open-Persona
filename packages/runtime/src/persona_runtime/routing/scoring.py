"""Spec 18 Layer 2 sweet-spot scoring (T10; D-18-2, D-18-5, D-18-X-partial-metadata-behaviour).

The :class:`UnifiedRouter` Layer 2 path scores each Layer 1-filtered tier on
a weighted combination of cost, quality fit, and first-token latency. Weights
are per-:data:`RoutingProfile` (D-18-2 lean values; v0.1 hard-coded, env-var
tuning lands v0.2 if telemetry surfaces drift).

The :func:`quality_proxy` formula (D-18-5) combines six weighted signals
into ``[0.0, 1.0]`` — generalises Spec 05's categorical rules into the
continuous scoring shape Layer 2 needs.

**Partial-metadata behaviour** (D-18-X-partial-metadata-behaviour): tiers
whose metadata is missing return ``None`` from :func:`score_tier`. The
UnifiedRouter excludes such tiers from Layer 2's choice and surfaces the
omission via the fallback rate when ALL filtered candidates lack metadata.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from persona_runtime.routing.types import RoutingContext
    from persona_runtime.tier import TierRegistry

__all__ = [
    "PROFILE_WEIGHTS",
    "ProfileWeights",
    "quality_proxy",
    "score_tier",
]


class ProfileWeights:
    """Per-profile (cost / quality / latency) weight bundle.

    A small data container — frozen via direct attribute discipline rather
    than Pydantic, because :data:`PROFILE_WEIGHTS` is a module-level constant
    and reaches no boundary (D-05-9 boundary-types-are-Pydantic doesn't apply
    to module-private value objects).
    """

    __slots__ = ("cost", "latency", "quality")

    def __init__(self, *, cost: float, quality: float, latency: float) -> None:
        self.cost = cost
        self.quality = quality
        self.latency = latency

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return f"ProfileWeights(cost={self.cost}, quality={self.quality}, latency={self.latency})"


PROFILE_WEIGHTS: dict[str, ProfileWeights] = {
    "text_default": ProfileWeights(cost=0.40, quality=0.50, latency=0.10),
    "voice": ProfileWeights(cost=0.10, quality=0.30, latency=0.60),
}
"""D-18-2 lean per-profile factor weights.

Voice's 0.60 latency weight reflects R-18-1's finding that voice routing's
own latency budget (30ms per D-18-4) demands every routing decision optimise
hard for latency. Text balances cost (40%) against quality (50%).
"""

_TIER_QUALITY_ESTIMATES: dict[str, float] = {
    "frontier": 1.0,
    "mid": 0.5,
    "small": 0.0,
}
"""Tier-level quality estimate used by :func:`score_tier`'s ``quality_fit``.

Frontier tiers are assumed capable of the highest-quality response; small
tiers are assumed lowest. The fit metric is ``1 - |quality_proxy - estimate|``
so a turn with high quality_proxy fits frontier well and small poorly.
"""

# Normalisation anchors (D-18-3 Phase 4 leans; tunable in v0.2 if telemetry
# surfaces that real distributions cluster elsewhere).
_COST_NORMALISATION_CENTS = 5.0
"""Cost normalisation anchor — a turn this expensive scores 0.0 on the cost axis."""

_LATENCY_NORMALISATION_MS = 5000.0
"""Latency normalisation anchor — a tier this slow scores 0.0 on the latency axis."""

_ESTIMATED_OUTPUT_TOKENS_DEFAULT = 500
"""Conservative output-token estimate for cost calculation (per D-18-5 §3.1)."""


def quality_proxy(context: RoutingContext) -> float:
    """D-18-5 six-signal weighted sum — output ``[0.0, 1.0]``.

    Higher = the turn needs a stronger model. Generalises the Spec 05
    categorical rules (first-turn → frontier, identity-sensitive → frontier)
    as continuous contributions Layer 2 can weigh against cost + latency.

    Args:
        context: The turn's routing context.

    Returns:
        A float in ``[0.0, 1.0]`` (clamped). Constants are hard-coded v0.1
        per D-18-5; same env-var tuning path as :data:`PROFILE_WEIGHTS`
        in v0.2.
    """
    score = (
        0.30 * float(context.is_first_turn)
        + 0.30 * float(context.is_identity_sensitive)
        + 0.15 * float(context.requires_strong_tools)
        + 0.10 * float(context.requires_vision)
        + 0.10 * min(1.0, context.estimated_input_tokens / 4000.0)
        + 0.05 * float(context.conversation_phase in {"opening", "closing"})
    )
    return min(1.0, score)


def score_tier(
    tier: str,
    context: RoutingContext,
    tier_registry: TierRegistry,
) -> float | None:
    """Score a single tier on the Layer 2 axes — returns ``None`` if metadata absent.

    The score combines:

    * ``quality_fit = 1 - |quality_proxy(context) - tier's quality estimate|``
      — how well the tier's capability matches the turn's needs.
    * ``normalised_cost = 1 - clamp(per_turn_cost / 5¢, 0, 1)`` — higher = cheaper.
    * ``normalised_latency = 1 - clamp(first_token_latency / 5000ms, 0, 1)``
      — higher = faster.

    Combined per-:data:`PROFILE_WEIGHTS`. Tier ordering preserved as
    tie-breaker by the caller (``UnifiedRouter``).

    Args:
        tier: The tier name to score.
        context: The turn's routing context.
        tier_registry: The :class:`TierRegistry` (read-only —
            :meth:`metadata_for` does NOT instantiate backends).

    Returns:
        A float score (typically ``[0.0, 1.0]``; may go slightly above 1.0
        when quality_fit is extreme — not enforced because clamping would
        lose useful relative-ordering signal). ``None`` when the tier's
        :class:`TierMetadata` is absent — D-18-X-partial-metadata-behaviour
        excludes the tier from Layer 2 scoring.
    """
    md = tier_registry.metadata_for(tier)
    if md is None:
        return None

    weights = PROFILE_WEIGHTS.get(context.profile)
    if weights is None:
        # Unknown profile — degrade gracefully via text_default. v0.2
        # extensibility lands as a new entry in PROFILE_WEIGHTS, not as a
        # signature change.
        weights = PROFILE_WEIGHTS["text_default"]

    q_target = quality_proxy(context)
    q_tier = _TIER_QUALITY_ESTIMATES.get(tier, 0.5)
    quality_fit = 1.0 - abs(q_target - q_tier)

    cost_per_turn = (
        md.cost_input_per_1k_tokens * context.estimated_input_tokens / 1000.0
        + md.cost_output_per_1k_tokens * _ESTIMATED_OUTPUT_TOKENS_DEFAULT / 1000.0
    )
    normalised_cost = max(0.0, 1.0 - min(1.0, cost_per_turn / _COST_NORMALISATION_CENTS))
    normalised_latency = max(
        0.0, 1.0 - min(1.0, md.first_token_latency_ms / _LATENCY_NORMALISATION_MS)
    )

    return (
        weights.cost * normalised_cost
        + weights.quality * quality_fit
        + weights.latency * normalised_latency
    )
