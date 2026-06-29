"""Unit tests for the conversational state-machine vocabulary (spec V4 T01)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
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

# ---------- the four canonical transitions ----------------------------------


def test_listening_to_user_speaking_on_speech_onset() -> None:
    assert (
        advance(ConversationalState.LISTENING, TransitionTrigger.USER_SPEECH_STARTED)
        == ConversationalState.USER_SPEAKING
    )


def test_user_speaking_to_processing_on_turn_end() -> None:
    assert (
        advance(ConversationalState.USER_SPEAKING, TransitionTrigger.TURN_ENDED)
        == ConversationalState.PROCESSING
    )


def test_processing_to_persona_speaking_on_first_audio() -> None:
    assert (
        advance(ConversationalState.PROCESSING, TransitionTrigger.MODEL_FIRST_AUDIO)
        == ConversationalState.PERSONA_SPEAKING
    )


def test_persona_speaking_to_listening_on_finish() -> None:
    assert (
        advance(ConversationalState.PERSONA_SPEAKING, TransitionTrigger.PERSONA_FINISHED)
        == ConversationalState.LISTENING
    )


# ---------- the two judgement transitions (V4's essence) --------------------


def test_barge_in_is_legal_persona_speaking_to_user_speaking() -> None:
    assert (
        advance(ConversationalState.PERSONA_SPEAKING, TransitionTrigger.BARGE_IN)
        == ConversationalState.USER_SPEAKING
    )
    assert is_legal_transition(
        ConversationalState.PERSONA_SPEAKING, ConversationalState.USER_SPEAKING
    )


def test_user_continuation_during_processing_is_legal() -> None:
    """D-V4-5 — user re-speaks before the persona starts → continuation."""
    assert (
        advance(ConversationalState.PROCESSING, TransitionTrigger.USER_CONTINUATION)
        == ConversationalState.USER_SPEAKING
    )


# ---------- illegal transitions reject ------------------------------------


def test_listening_to_persona_speaking_skipping_processing_is_illegal() -> None:
    """The canonical illegal move — the persona cannot speak without a turn."""
    assert not is_legal_transition(
        ConversationalState.LISTENING, ConversationalState.PERSONA_SPEAKING
    )
    with pytest.raises(InvalidConversationalTransitionError):
        advance(ConversationalState.LISTENING, TransitionTrigger.MODEL_FIRST_AUDIO)


def test_user_speaking_to_persona_speaking_skipping_processing_is_illegal() -> None:
    assert not is_legal_transition(
        ConversationalState.USER_SPEAKING, ConversationalState.PERSONA_SPEAKING
    )
    with pytest.raises(InvalidConversationalTransitionError):
        advance(ConversationalState.USER_SPEAKING, TransitionTrigger.MODEL_FIRST_AUDIO)


def test_listening_to_processing_via_user_turn_trigger_is_illegal() -> None:
    """A USER-turn trigger still cannot skip USER_SPEAKING to reach PROCESSING.

    V10-D-2 adds a LISTENING→PROCESSING edge, but ONLY via the agent-initiated
    narration trigger — a user-turn trigger (TURN_ENDED) from LISTENING stays
    illegal, so the user path still cannot skip USER_SPEAKING.
    """
    with pytest.raises(InvalidConversationalTransitionError):
        advance(ConversationalState.LISTENING, TransitionTrigger.TURN_ENDED)


# ---------- V10-D-2: agent-initiated narration (floor-gated) -----------------


def test_listening_to_processing_on_agent_initiated_is_legal() -> None:
    """V10-D-2 — the floor-gated artifact narration enters PROCESSING (not a raw
    LISTENING→PERSONA_SPEAKING skip), so it reuses the existing
    PROCESSING→PERSONA_SPEAKING (audio) and PROCESSING→LISTENING (no-audio RESET)
    edges and the no-speaking-without-a-turn invariant is preserved."""
    assert (
        advance(ConversationalState.LISTENING, TransitionTrigger.AGENT_INITIATED)
        == ConversationalState.PROCESSING
    )
    assert is_legal_transition(ConversationalState.LISTENING, ConversationalState.PROCESSING)


def test_agent_initiated_is_legal_only_from_listening() -> None:
    """The floor gate at the FSM level: agent-initiated narration may ONLY start
    from a genuinely idle floor (LISTENING). From any other state the trigger is
    illegal — the persona can never inject an unsolicited turn while the user
    holds the floor or while it is already mid-turn."""
    for state in (
        ConversationalState.USER_SPEAKING,
        ConversationalState.PROCESSING,
        ConversationalState.PERSONA_SPEAKING,
        ConversationalState.PREPARING,
    ):
        with pytest.raises(InvalidConversationalTransitionError):
            advance(state, TransitionTrigger.AGENT_INITIATED)


def test_agent_initiated_does_not_make_listening_to_persona_speaking_legal() -> None:
    """The narration goes through PROCESSING; the canonical illegal skip
    (LISTENING→PERSONA_SPEAKING) stays illegal."""
    assert not is_legal_transition(
        ConversationalState.LISTENING, ConversationalState.PERSONA_SPEAKING
    )


def test_illegal_transition_error_carries_context() -> None:
    with pytest.raises(InvalidConversationalTransitionError) as exc_info:
        advance(ConversationalState.LISTENING, TransitionTrigger.BARGE_IN)
    assert exc_info.value.context["current"] == "listening"
    assert exc_info.value.context["trigger"] == "barge_in"


# ---------- same-state re-entry is an idempotent no-op ----------------------


def test_same_state_is_always_legal() -> None:
    for state in ConversationalState:
        assert is_legal_transition(state, state)


# ---------- reset path (watchdog / degradation) ----------------------------


@pytest.mark.parametrize(
    "from_state",
    [
        ConversationalState.USER_SPEAKING,
        ConversationalState.PROCESSING,
        ConversationalState.PERSONA_SPEAKING,
    ],
)
def test_reset_forces_back_to_listening(from_state: ConversationalState) -> None:
    """D-V4-X-watchdog-timeout / D-V4-6 — RESET is the always-available
    escape back to the user's floor."""
    assert advance(from_state, TransitionTrigger.RESET) == ConversationalState.LISTENING


# ---------- projections -----------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ConversationalState.LISTENING, AgentState.LISTENING),
        (ConversationalState.USER_SPEAKING, AgentState.LISTENING),
        (ConversationalState.PROCESSING, AgentState.THINKING),
        (ConversationalState.PERSONA_SPEAKING, AgentState.SPEAKING),
    ],
)
def test_agent_state_projection(state: ConversationalState, expected: AgentState) -> None:
    assert agent_state_for(state) == expected


def test_agent_speaking_only_during_persona_speaking() -> None:
    """The mute-window provider (D-V2-X-echo-cancellation) keys on this."""
    speaking = [s for s in ConversationalState if agent_state_for(s) == AgentState.SPEAKING]
    assert speaking == [ConversationalState.PERSONA_SPEAKING]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ConversationalState.LISTENING, UserState.LISTENING),
        (ConversationalState.USER_SPEAKING, UserState.SPEAKING),
        (ConversationalState.PROCESSING, UserState.LISTENING),
        (ConversationalState.PERSONA_SPEAKING, UserState.LISTENING),
    ],
)
def test_user_state_projection(state: ConversationalState, expected: UserState) -> None:
    assert user_state_for(state) == expected


# ---------- transition record ----------------------------------------------


def test_transition_record_is_frozen_and_forbids_extra() -> None:
    from pydantic import ValidationError

    t = ConversationalTransition(
        from_state=ConversationalState.LISTENING,
        to_state=ConversationalState.USER_SPEAKING,
        trigger=TransitionTrigger.USER_SPEECH_STARTED,
        at=datetime.now(UTC),
    )
    assert t.from_state == ConversationalState.LISTENING
    with pytest.raises(ValidationError):
        ConversationalTransition(
            from_state=ConversationalState.LISTENING,
            to_state=ConversationalState.USER_SPEAKING,
            trigger=TransitionTrigger.USER_SPEECH_STARTED,
            at=datetime.now(UTC),
            extra="nope",  # type: ignore[call-arg]
        )


def test_states_are_json_safe_strings() -> None:
    """StrEnum members round-trip as their wire string (D-05-9)."""
    assert ConversationalState.PERSONA_SPEAKING == "persona_speaking"
    assert TransitionTrigger.BARGE_IN == "barge_in"
    assert AgentState.SPEAKING == "speaking"


# ---------- Spec 32 A2: the greet-first PREPARING entry path ----------------
#
# Greet-first makes the persona speak turn 0 with NO prior user turn. Today the
# only legal entry to PERSONA_SPEAKING is MODEL_FIRST_AUDIO *from* PROCESSING,
# which requires a user turn — so a new initial state PREPARING (the persona is
# generating turn 0 while the call "rings", mic gated) gets its own legal entry
# to PERSONA_SPEAKING. The transition is added to the table, not bolted around it
# (D-32-X / A2). All existing transitions above are unchanged.


def test_preparing_to_persona_speaking_on_first_greeting_audio() -> None:
    """Turn 0's first audio moves the greeting out of PREPARING into speaking."""
    assert (
        advance(ConversationalState.PREPARING, TransitionTrigger.MODEL_FIRST_AUDIO)
        == ConversationalState.PERSONA_SPEAKING
    )
    assert is_legal_transition(ConversationalState.PREPARING, ConversationalState.PERSONA_SPEAKING)


def test_preparing_resets_to_listening_on_turn0_degrade() -> None:
    """Turn-0 timeout / failure (the 'never ring forever' degrade, D-32-3) drops
    the call to the user's floor so they can still talk — never a stuck ring."""
    assert (
        advance(ConversationalState.PREPARING, TransitionTrigger.RESET)
        == ConversationalState.LISTENING
    )


def test_user_speech_started_is_illegal_during_preparing() -> None:
    """The mic is gated through the greeting (D-32-5); a user onset cannot enter
    PREPARING. The FSM rejects it structurally rather than colliding turn 0 with
    the user's first words."""
    assert not is_legal_transition(ConversationalState.PREPARING, ConversationalState.USER_SPEAKING)
    with pytest.raises(InvalidConversationalTransitionError):
        advance(ConversationalState.PREPARING, TransitionTrigger.USER_SPEECH_STARTED)


def test_user_speech_started_is_legal_only_from_listening() -> None:
    """The greeting-end → mic-enable hand-off race invariant (the 68e5155 class):
    a user's first *normal* turn (USER_SPEECH_STARTED) is legal ONLY once the FSM
    is back in LISTENING — never during PREPARING (greeting being prepared) nor
    PERSONA_SPEAKING (greeting playing; an onset there is a BARGE_IN, not a new
    turn). So the user's first turn cannot be processed mid-greeting."""
    legal_sources = [
        state
        for state in ConversationalState
        if is_legal_transition(state, ConversationalState.USER_SPEAKING)
        and state is not ConversationalState.USER_SPEAKING  # exclude same-state no-op
    ]
    # LISTENING via USER_SPEECH_STARTED; PERSONA_SPEAKING via BARGE_IN;
    # PROCESSING via USER_CONTINUATION. PREPARING must NOT appear.
    assert ConversationalState.PREPARING not in legal_sources
    # And specifically: the new-turn trigger is legal only from LISTENING.
    assert (
        advance(ConversationalState.LISTENING, TransitionTrigger.USER_SPEECH_STARTED)
        == ConversationalState.USER_SPEAKING
    )
    for state in ConversationalState:
        if state is ConversationalState.LISTENING:
            continue
        with pytest.raises(InvalidConversationalTransitionError):
            advance(state, TransitionTrigger.USER_SPEECH_STARTED)


def test_preparing_does_not_skip_to_listening_without_audio_or_reset() -> None:
    """Only MODEL_FIRST_AUDIO (greeting starts) or RESET (degrade) leave PREPARING;
    a stray TURN_ENDED/BARGE_IN/PERSONA_FINISHED is illegal there."""
    for trigger in (
        TransitionTrigger.TURN_ENDED,
        TransitionTrigger.BARGE_IN,
        TransitionTrigger.PERSONA_FINISHED,
        TransitionTrigger.USER_CONTINUATION,
    ):
        with pytest.raises(InvalidConversationalTransitionError):
            advance(ConversationalState.PREPARING, trigger)


def test_preparing_agent_projection_is_thinking() -> None:
    """While preparing turn 0 the persona is self-driven (no audio yet) → THINKING,
    NOT SPEAKING — so the echo-mute window (D-V2-X) does not open early and the orb
    shows a 'getting ready' motion, never a frozen speaking state."""
    assert agent_state_for(ConversationalState.PREPARING) == AgentState.THINKING
    # The mute-window invariant still holds: only PERSONA_SPEAKING is SPEAKING.
    speaking = [s for s in ConversationalState if agent_state_for(s) == AgentState.SPEAKING]
    assert speaking == [ConversationalState.PERSONA_SPEAKING]


def test_preparing_user_projection_is_listening() -> None:
    """The user is silent/attending while the call rings (mic gated)."""
    assert user_state_for(ConversationalState.PREPARING) == UserState.LISTENING


def test_preparing_is_json_safe_string() -> None:
    assert ConversationalState.PREPARING == "preparing"
