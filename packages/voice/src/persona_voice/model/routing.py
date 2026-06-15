"""Voice routing policy — first-token latency as a hard gate (spec V5 T2; D-V5-2).

For voice, first-token latency is the binding factor: a model great for text
quality but slow to first token makes the conversation feel laggy regardless of
STT/TTS/transport (R-V5-1 — anything over ~600 ms TTFT eats the ~800 ms
voice-to-voice budget). The tier decision stays rule-based and instant (Spec 05,
unchanged); V5 adds a **within-tier** first-token-latency gate.

:class:`VoiceRoutingPolicy` is the thin voice layer over Spec 23's
:class:`~persona_runtime.routing.IntelligentRouter`: it passes a hard TTFT
``candidate_filter`` (gate) and lets the IntelligentRouter score the survivors —
"gate-then-score, layered on the IntelligentRouter" (D-V5-2), mirroring Spec 23's
own capability-gate-then-score shape. The gate reads the static, provider-
qualified ``ModelMetadata.latency_p50_ms`` (D-V5-2-latency-granularity); the live
per-model :class:`~persona_runtime.routing.FirstTokenLatencyTracker` still refines
the *score* through the IntelligentRouter's existing ``latency_override`` plumbing
(no tracker edit). On any miss / empty gated set the IntelligentRouter degrades to
the rule-based slot-0 model — the turn never strands.

Non-reasoning is forced at *generation* time (T4), not here: reasoning is a
request parameter, not a model-selection attribute (``ModelMetadata`` has no
reasoning flag), so forcing it belongs on the ``chat_stream`` call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona_runtime.routing import ModelSelection

if TYPE_CHECKING:
    from persona.backends.model_metadata import ModelMetadata
    from persona_runtime.routing import RoutingContext

    from persona_voice.model.turn_context import VoiceTurnContext

__all__ = ["DEFAULT_VOICE_TTFT_GATE_MS", "VoiceRoutingPolicy"]

#: The hard first-token-latency ceiling for voice model selection (ms, P50).
#: R-V5-1: the LLM's slice of the ~800 ms voice-to-voice budget is ~200–500 ms;
#: ~600 ms is the practical gate above which voice feels laggy. Constructor-
#: tunable per deployment.
DEFAULT_VOICE_TTFT_GATE_MS = 600.0


class VoiceRoutingPolicy:
    """Within-tier model selection under a hard first-token-latency gate (D-V5-2).

    Args:
        ttft_gate_ms: The hard P50 first-token-latency ceiling (ms). Models whose
            static ``latency_p50_ms`` exceeds it are excluded *before* scoring.

    Raises:
        ValueError: ``ttft_gate_ms`` is not positive (fail-fast config check;
            mirrors :class:`~persona_runtime.routing.FirstTokenLatencyTracker`).
    """

    def __init__(self, *, ttft_gate_ms: float = DEFAULT_VOICE_TTFT_GATE_MS) -> None:
        if ttft_gate_ms <= 0:
            msg = f"ttft_gate_ms must be > 0; got {ttft_gate_ms!r}"
            raise ValueError(msg)
        self._gate_ms = ttft_gate_ms

    @property
    def ttft_gate_ms(self) -> float:
        """The configured hard first-token-latency ceiling (ms, P50)."""
        return self._gate_ms

    def select_model(
        self,
        context: VoiceTurnContext,
        *,
        tier: str,
        routing_context: RoutingContext,
        session_spent_cents: float = 0.0,
        day_spent_cents: float = 0.0,
    ) -> ModelSelection:
        """Choose the best model in ``tier`` that meets the voice TTFT gate.

        When intelligent routing is enabled for the persona, delegates to the
        IntelligentRouter with the TTFT gate as the ``candidate_filter``
        (gate-then-score). When it is disabled (or no IntelligentRouter is
        wired), returns the rule-based slot-0 model unchanged — voice still
        works, just without within-tier latency optimisation.

        Args:
            context: The session-bound runtime collaborators.
            tier: The already-chosen tier (rule-based; Spec 05).
            routing_context: This turn's routing context (capability + tokens).
            session_spent_cents: Loop-owned per-session spend (budget ramp).
            day_spent_cents: Per-day spend (0.0 — no cross-session store, D-23-7).

        Returns:
            The :class:`ModelSelection`; ``fallback_engaged`` is ``True`` on the
            disabled path and on a gate/metadata degrade to slot-0.
        """
        persona = context.persona
        router = context.intelligent_router
        if router is None or not persona.routing.intelligent.enabled:
            return ModelSelection(
                model=context.tier_registry.model_name_for(tier),
                fallback_engaged=True,
                fallback_reason="intelligent_routing_disabled",
            )
        return router.select_model(
            tier,
            routing_context,
            intelligent=persona.routing.intelligent,
            budget=persona.routing.budget,
            session_spent_cents=session_spent_cents,
            day_spent_cents=day_spent_cents,
            candidate_filter=self._meets_voice_ttft,
        )

    def _meets_voice_ttft(self, canonical_id: str, metadata: ModelMetadata) -> bool:  # noqa: ARG002 — candidate_filter contract is (id, metadata); the gate reads only latency
        """The hard TTFT gate — static provider-qualified ``latency_p50_ms``."""
        return metadata.latency_p50_ms <= self._gate_ms
