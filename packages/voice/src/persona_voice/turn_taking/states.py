"""Conversational state-machine vocabulary for persona-voice (spec V4 T01).

This module materialises the live conversational state machine the V4
orchestration drives — the four states a call moves through (spec V4 §4) and
the guarded transitions between them. V1 *named* the vocabulary in its
research (``UserState`` / ``AgentState``) but shipped no Python types; V4
materialises it here as frozen Pydantic + a trigger-driven finite state
machine.

The canonical state is :class:`ConversationalState`. ``AgentState`` and
``UserState`` are **derived projections** of it (via :func:`agent_state_for`
/ :func:`user_state_for`), not independently-mutable enums — there is one
source of truth, so the agent-speaking mute-window provider (V2's
echo-cancellation carry-forward, D-V2-X-echo-cancellation) and V6's UI can
read consistent sub-views without risk of divergence.

The machine is trigger-driven: :func:`advance` maps ``(current, trigger)``
to the next state, raising :class:`InvalidConversationalTransitionError` for
an illegal combination. The two judgement transitions are V4's essence:
``USER_SPEAKING → PROCESSING`` (turn-taking — was that the end of the turn?)
and ``PERSONA_SPEAKING → USER_SPEAKING`` (barge-in — is that a real
interruption?). Skipping ``PROCESSING`` (e.g. ``LISTENING →
PERSONA_SPEAKING``) is structurally illegal — the persona cannot speak
without a turn to respond to.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime for Pydantic field validation
from enum import StrEnum

from persona.errors import PersonaError
from pydantic import BaseModel, ConfigDict

__all__ = [
    "AgentState",
    "ConversationalState",
    "ConversationalTransition",
    "InvalidConversationalTransitionError",
    "TransitionTrigger",
    "UserState",
    "advance",
    "agent_state_for",
    "is_legal_transition",
    "user_state_for",
]


class ConversationalState(StrEnum):
    """The live state of a call — who has the floor (spec V4 §4).

    Encoded as ``str`` so it survives JSON round-trips into the T10 VoiceLog
    / operator-pass record and V6's state-broadcast surface (the
    cross-process-safe boundary form per D-05-9).
    """

    PREPARING = "preparing"
    """Greet-first opening: the persona is generating turn 0 (the greeting) with
    no user input while the call "rings" and the mic is gated (Spec 32 Feature A).
    The only entry to PERSONA_SPEAKING that does not require a prior user turn."""
    LISTENING = "listening"
    """The floor is the user's; the persona is silent, waiting (user silent)."""
    USER_SPEAKING = "user_speaking"
    """V2 reports speech; V4 accumulates the transcript, watches for turn-end."""
    PROCESSING = "processing"
    """The user's turn ended; V4 invoked the model and awaits/streams the reply."""
    PERSONA_SPEAKING = "persona_speaking"
    """V3 plays the reply; V4 watches V2 for a barge-in."""


class AgentState(StrEnum):
    """Persona-side projection of :class:`ConversationalState`.

    Mirrors the V1-research ``AgentState`` naming. ``SPEAKING`` is the value
    the echo-cancellation mute-window provider keys on (D-V2-X-echo-
    cancellation): while the agent speaks, V2's VAD must be gated so the
    persona's own voice does not trigger a false barge-in.
    """

    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class UserState(StrEnum):
    """User-side projection of :class:`ConversationalState`.

    Mirrors the V1-research ``UserState`` naming. ``LISTENING`` here means the
    user is silent and attending (the persona has the floor or the call is
    idle), distinct from the agent's ``LISTENING``.
    """

    SPEAKING = "speaking"
    LISTENING = "listening"


class TransitionTrigger(StrEnum):
    """The events that drive a conversational-state transition.

    These are V4 *decisions* (or terminal pipeline events), not raw sensor
    events — the orchestrator translates V2's activity signal + the
    controller/detector verdicts into these triggers.
    """

    USER_SPEECH_STARTED = "user_speech_started"
    """A confirmed user speech-onset while the floor was the user's (LISTENING)."""
    TURN_ENDED = "turn_ended"
    """The TurnTakingController decided the user's turn is over (END_TURN)."""
    MODEL_FIRST_AUDIO = "model_first_audio"
    """The first persona audio chunk reached the outbound rail."""
    BARGE_IN = "barge_in"
    """A confirmed interruption while the persona was speaking (D-V4-2)."""
    USER_CONTINUATION = "user_continuation"
    """The user re-spoke during PROCESSING — a continuation (D-V4-5)."""
    PERSONA_FINISHED = "persona_finished"
    """The persona finished its reply normally; the floor returns to the user."""
    RESET = "reset"
    """Watchdog / graceful-degradation / error path: force the floor back to LISTENING."""
    GREETING_STARTED = "greeting_started"
    """Greet-first opening signal (Spec 32 A4): the agent has joined and is
    preparing turn 0. **Broadcast-only** — it announces the initial PREPARING
    state to the client (ring + gate the mic); it is NOT in the transition table
    (PREPARING is the call's initial state, not entered via a transition)."""
    AGENT_INITIATED = "agent_initiated"
    """V10-D-2: the persona starts an UNSOLICITED turn from the idle floor — the
    floor-gated narration of an async artifact that finished while it was the
    user's floor (e.g. "I've put the diagram on screen"). Legal ONLY from
    LISTENING (a genuinely idle floor); it enters PROCESSING (NOT a raw
    LISTENING→PERSONA_SPEAKING skip), so it reuses the existing
    PROCESSING→PERSONA_SPEAKING (first audio) and PROCESSING→LISTENING (no-audio
    RESET) edges and the no-speaking-without-a-turn invariant holds."""


# The finite state machine, keyed by ``(current_state, trigger)`` → next
# state. A ``(current, trigger)`` pair absent from this table is an illegal
# transition (:func:`advance` raises). Same-state re-entry is handled by
# :func:`advance` as an idempotent no-op and is NOT listed here.
_TRANSITIONS: dict[tuple[ConversationalState, TransitionTrigger], ConversationalState] = {
    # Greet-first (Spec 32 A2): turn 0's first audio moves the persona's opening
    # line out of PREPARING into PERSONA_SPEAKING — the ONLY entry to speaking that
    # does not pass through PROCESSING (the greeting has no user turn to respond
    # to). Note this does NOT make LISTENING → PERSONA_SPEAKING legal: only the
    # dedicated PREPARING source has this entry, so the canonical illegal skip is
    # preserved.
    (ConversationalState.PREPARING, TransitionTrigger.MODEL_FIRST_AUDIO): (
        ConversationalState.PERSONA_SPEAKING
    ),
    # Turn-0 timeout / failure (the "never ring forever" degrade, D-32-3) drops to
    # the user's floor so they can still talk — the ring never hangs.
    (ConversationalState.PREPARING, TransitionTrigger.RESET): (ConversationalState.LISTENING),
    (ConversationalState.LISTENING, TransitionTrigger.USER_SPEECH_STARTED): (
        ConversationalState.USER_SPEAKING
    ),
    # V10-D-2: the persona narrates a ready async artifact from the idle floor.
    # Legal ONLY from LISTENING, and it lands in PROCESSING — the narration is a
    # real (persona-initiated) turn that then reaches PERSONA_SPEAKING via
    # MODEL_FIRST_AUDIO, exactly like any turn. This does NOT make
    # LISTENING→PERSONA_SPEAKING legal (the canonical illegal skip stands); only
    # the dedicated PREPARING source has the speak-without-PROCESSING entry.
    (ConversationalState.LISTENING, TransitionTrigger.AGENT_INITIATED): (
        ConversationalState.PROCESSING
    ),
    (ConversationalState.USER_SPEAKING, TransitionTrigger.TURN_ENDED): (
        ConversationalState.PROCESSING
    ),
    # User went silent without a real turn (false start / no content) → back
    # to listening (graceful, RESET).
    (ConversationalState.USER_SPEAKING, TransitionTrigger.RESET): (ConversationalState.LISTENING),
    (ConversationalState.PROCESSING, TransitionTrigger.MODEL_FIRST_AUDIO): (
        ConversationalState.PERSONA_SPEAKING
    ),
    # User re-spoke before the persona started speaking (D-V4-5 continuation).
    (ConversationalState.PROCESSING, TransitionTrigger.USER_CONTINUATION): (
        ConversationalState.USER_SPEAKING
    ),
    # Empty / cancelled reply with no audio produced, or watchdog reset.
    (ConversationalState.PROCESSING, TransitionTrigger.RESET): (ConversationalState.LISTENING),
    # The barge-in transition — V4's essence.
    (ConversationalState.PERSONA_SPEAKING, TransitionTrigger.BARGE_IN): (
        ConversationalState.USER_SPEAKING
    ),
    (ConversationalState.PERSONA_SPEAKING, TransitionTrigger.PERSONA_FINISHED): (
        ConversationalState.LISTENING
    ),
    # Watchdog hard-timeout (D-V4-X-watchdog-timeout) forces the floor back.
    (ConversationalState.PERSONA_SPEAKING, TransitionTrigger.RESET): (
        ConversationalState.LISTENING
    ),
}


class InvalidConversationalTransitionError(PersonaError):
    """Raised on an illegal conversational-state transition.

    A backward / skipping transition (e.g. ``LISTENING`` →
    ``PERSONA_SPEAKING`` without passing through ``PROCESSING``, or a trigger
    that makes no sense in the current state) is surfaced as a domain error
    rather than silently corrupting the floor ownership — the same
    fail-loud discipline :class:`persona_voice.session.state_machine.
    InvalidSessionStateError` applies to the session lifecycle.
    """


def advance(
    current: ConversationalState,
    trigger: TransitionTrigger,
) -> ConversationalState:
    """Return the next conversational state for ``(current, trigger)``.

    Same-state re-entry (a trigger whose target equals ``current``) is an
    idempotent no-op — it returns ``current`` without error so the
    orchestrator can fire a trigger defensively without tracking whether the
    machine already moved.

    Args:
        current: The current conversational state.
        trigger: The decision/event driving the transition.

    Returns:
        The next :class:`ConversationalState`.

    Raises:
        InvalidConversationalTransitionError: ``(current, trigger)`` is not a
            legal transition.
    """
    target = _TRANSITIONS.get((current, trigger))
    if target is None:
        msg = f"illegal conversational transition: {current} --{trigger}-->"
        raise InvalidConversationalTransitionError(
            msg,
            context={"current": str(current), "trigger": str(trigger)},
        )
    return target


def is_legal_transition(
    from_state: ConversationalState,
    to_state: ConversationalState,
) -> bool:
    """Whether some trigger moves ``from_state`` to ``to_state``.

    Same-state is always legal (the idempotent no-op of :func:`advance`).
    Used by V6 / tests to validate a state pair without naming the trigger
    (e.g. ``PERSONA_SPEAKING → USER_SPEAKING`` is legal — barge-in;
    ``LISTENING → PERSONA_SPEAKING`` is illegal — skips PROCESSING).
    """
    if from_state == to_state:
        return True
    return any(
        src == from_state and dst == to_state for (src, _trigger), dst in _TRANSITIONS.items()
    )


def agent_state_for(state: ConversationalState) -> AgentState:
    """Project the conversational state onto the persona-side view."""
    if state in (ConversationalState.PROCESSING, ConversationalState.PREPARING):
        # PREPARING (generating turn 0) is self-driven with no audio yet — same
        # THINKING projection as PROCESSING, so the echo-mute window (D-V2-X) does
        # not open before the greeting actually plays.
        return AgentState.THINKING
    if state == ConversationalState.PERSONA_SPEAKING:
        return AgentState.SPEAKING
    # LISTENING and USER_SPEAKING: the agent is attending, not speaking.
    return AgentState.LISTENING


def user_state_for(state: ConversationalState) -> UserState:
    """Project the conversational state onto the user-side view."""
    if state == ConversationalState.USER_SPEAKING:
        return UserState.SPEAKING
    return UserState.LISTENING


class ConversationalTransition(BaseModel):
    """An observed state transition — the V6 / audit hook record (frozen).

    Carries ``at`` as an explicit caller-supplied UTC instant (the
    orchestrator injects its clock; this module never reads the wall clock)
    so transition timelines are deterministic in tests and reproducible in
    the operator-pass evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_state: ConversationalState
    to_state: ConversationalState
    trigger: TransitionTrigger
    at: datetime
