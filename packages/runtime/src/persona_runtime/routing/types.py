"""Spec 18 boundary types (T02; D-18-X-turnlog-extension, D-18-X-protocol-location).

:class:`RoutingContext` is the turn-side input to :meth:`Router.route`: the
hard requirements (vision / tokens / tools), the conversation/persona signals
(first turn / identity-sensitive / phase), and the routing profile
(``text_default`` / ``voice``). Every fact the router reasons over.

:class:`RoutingDecision` is the router's output: the chosen tier + model +
rationale + candidates considered + Layer 1 filter explanations + Layer 2
score. Carried on :class:`~persona_runtime.logging.TurnLog` so routing quality
is measurable over time (R-18-4 evaluation harness).

Both are frozen Pydantic v2 + ``extra="forbid"`` — they cross the API /
Postgres boundary via TurnLog (D-05-9 / D-06-1 boundary-types-are-Pydantic
precedent).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RoutingContext",
    "RoutingDecision",
    "RoutingProfile",
]


RoutingProfile = Literal["text_default", "voice"]
"""The routing profile selects per-profile factor weights (D-18-2).

Extensible — a new profile (e.g., ``"agentic"``) lands by extending the
literal + adding a weight row. ``voice`` weights latency 0.60 (R-18-1 voice
budget); ``text_default`` balances cost 0.40 / quality 0.50.
"""


class RoutingContext(BaseModel):
    """All facts the router needs to decide a turn (T02).

    Built by the :class:`~persona_runtime.loop.ConversationLoop` (T06) from the
    persona, the conversation, and the current user message before the routing
    decision. Frozen + ``extra="forbid"`` so the boundary is unambiguous and
    extension is a deliberate decision, not accidental.

    Attributes:
        requires_vision: ``True`` when the current user message carries an
            image block. Drives Layer 1's vision constraint
            (D-18-X-layer1-extraction).
        estimated_input_tokens: Whole-prompt token count estimate (system +
            retrieved context + history + user message). Drives Layer 1's
            context-window constraint when registry metadata is present;
            contributes the small (0.10) token-bonus signal in D-18-5's
            ``quality_proxy`` formula.
        requires_strong_tools: ``True`` when the turn likely needs strong
            native-tool-calling (e.g., heavy code execution). v0.1 conservative
            default ``False``; T06 may refine.
        is_first_turn: ``conversation.turn_count == 0`` — the Spec 05 first-turn
            signal (D-18-5 ``quality_proxy`` 0.30 weight).
        is_identity_sensitive: The Spec 05 persona-critical signal (D-18-5
            ``quality_proxy`` 0.30 weight). Derived from the critical-phrase
            and persona-keyword classifiers.
        is_boilerplate: The Spec 05 boilerplate signal — acknowledgements,
            reformat requests, routine work that the small tier handles well.
            Defaults to ``False`` so callers that don't classify the message
            stay green; :class:`~persona_runtime.routing.heuristic.HeuristicRouter`'s
            :meth:`route` consults this for the boilerplate → small rule.
        conversation_phase: One of ``"opening"`` / ``"middle"`` / ``"closing"``
            — the small (0.05) phase signal in D-18-5. Kept as ``str`` (not
            Literal) so future phases (e.g., ``"escalating"``) extend without
            a boundary-type migration.
        profile: The :data:`RoutingProfile` literal driving Layer 2's per-profile
            weight selection (D-18-2). ``"voice"`` weights latency heavily.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    requires_vision: bool
    estimated_input_tokens: int = Field(ge=0)
    requires_strong_tools: bool
    is_first_turn: bool
    is_identity_sensitive: bool
    is_boilerplate: bool = False
    conversation_phase: str
    profile: RoutingProfile


class RoutingDecision(BaseModel):
    """The router's output for one turn (T02; D-18-X-turnlog-extension).

    Carried on :class:`~persona_runtime.logging.TurnLog` so routing quality is
    measurable over time (criterion 9 + R-18-4 evaluation harness). Frozen +
    ``extra="forbid"``; cross-boundary via TurnLog's Postgres JSONB column.

    Attributes:
        tier: The chosen tier name from the TierRegistry — usually
            ``"frontier"`` / ``"mid"`` / ``"small"`` but extensible.
        model: The concrete model name resolved from the tier's backend (e.g.,
            ``"claude-sonnet-4-6"``). Co-recording the model means TurnLog
            tells the full story even when the tier→model mapping evolves
            between deployments.
        rationale: Human-readable explanation of which signals dominated. The
            run-viewer surface (Spec 09 / F4) consumes this; the monthly review
            (D-18-X-monthly-review-cadence) reads aggregated rationale strings.
        candidates_considered: Tier names that survived Layer 1 (the filter
            set Layer 2 chose within). Ordered per
            :attr:`~persona_runtime.tier.TierRegistry.configured_tier_names`.
        layer1_filter_reasons: Per-tier explanation when a tier was filtered
            OUT by Layer 1 — e.g.,
            ``{"small": "no_vision_capability"}``. Empty when no filtering
            happened.
        layer2_score: The Layer 2 score the chosen tier received (range
            roughly ``[0.0, 1.0]``; not enforced because the scorer's
            normalisation may surface edge cases). ``0.0`` is the sentinel
            when :class:`HeuristicRouter` answered — the rationale names the
            firing rule instead of a numeric score.
        fallback_triggered: ``True`` when the :class:`UnifiedRouter` smart
            path failed (error or bound exceeded) and the decision came
            from the embedded :class:`HeuristicRouter` fallback. Always
            ``False`` from :class:`HeuristicRouter`. The
            :class:`~persona_runtime.logging.TurnLog` extension (T12) maps
            this onto its ``routing_fallback_triggered`` field
            (D-18-X-turnlog-extension).
        fallback_reason: One of ``"timeout"`` / ``"scoring_error"`` /
            ``"empty_metadata"`` / ``"partial_metadata:<tier>"`` when
            ``fallback_triggered`` is ``True``; ``None`` otherwise. Drives
            the D-18-X-fallback-instrumentation aggregate report at T13.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier: str
    model: str
    rationale: str
    candidates_considered: tuple[str, ...]
    layer1_filter_reasons: dict[str, str] = Field(default_factory=dict)
    layer2_score: float = 0.0
    fallback_triggered: bool = False
    fallback_reason: str | None = None
