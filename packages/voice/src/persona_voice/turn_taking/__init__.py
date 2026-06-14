"""persona-voice turn-taking, interruption, and orchestration (spec V4).

The orchestration core that turns V1's transport + V2's transcripts + V3's
interruptible synthesis into a conversation: the conversational state machine
(:mod:`persona_voice.turn_taking.states`), automatic endpointing, barge-in
interruption, the model-invocation turn cycle, barged-over memory honesty,
and full-loop latency attribution.

See ``docs/specs/phase2/spec_V4/`` for the spec, research, and decisions.
"""

from __future__ import annotations

from persona_voice.turn_taking.barge_in import (
    BargeInDecision,
    BargeInDetector,
    BargeInVerdict,
)
from persona_voice.turn_taking.bridge import (
    CompositeStateListener,
    HeardWordsBridge,
    LoopTurnActions,
    SessionEventBridge,
    wire_orchestrated_loop,
)
from persona_voice.turn_taking.controller import (
    DEFAULT_TURN_END_HOLD_TOKENS,
    TurnDecision,
    TurnTakingController,
    TurnVerdict,
)
from persona_voice.turn_taking.heard_words import (
    BargedReply,
    TurnTranscriptListener,
)
from persona_voice.turn_taking.latency import (
    HopAttribution,
    attribute_hops,
    compute_full_loop_ms,
)
from persona_voice.turn_taking.orchestrator import (
    AsyncioScheduler,
    ConversationalOrchestrator,
    ConversationalStateListener,
    Scheduler,
    SchedulerHandle,
    TurnActions,
)
from persona_voice.turn_taking.states import (
    AgentState,
    ConversationalState,
    ConversationalTransition,
    InvalidConversationalTransitionError,
    TransitionTrigger,
    UserState,
    advance,
    agent_state_for,
    is_legal_transition,
    user_state_for,
)

__all__ = [
    "DEFAULT_TURN_END_HOLD_TOKENS",
    "AgentState",
    "AsyncioScheduler",
    "BargeInDecision",
    "BargeInDetector",
    "BargeInVerdict",
    "BargedReply",
    "CompositeStateListener",
    "ConversationalOrchestrator",
    "ConversationalState",
    "ConversationalStateListener",
    "ConversationalTransition",
    "HeardWordsBridge",
    "HopAttribution",
    "InvalidConversationalTransitionError",
    "LoopTurnActions",
    "Scheduler",
    "SchedulerHandle",
    "SessionEventBridge",
    "TransitionTrigger",
    "TurnTranscriptListener",
    "TurnActions",
    "TurnDecision",
    "TurnTakingController",
    "TurnVerdict",
    "UserState",
    "advance",
    "agent_state_for",
    "attribute_hops",
    "compute_full_loop_ms",
    "is_legal_transition",
    "user_state_for",
    "wire_orchestrated_loop",
]
