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


def test_listening_to_processing_skipping_user_speaking_is_illegal() -> None:
    assert not is_legal_transition(ConversationalState.LISTENING, ConversationalState.PROCESSING)
    with pytest.raises(InvalidConversationalTransitionError):
        advance(ConversationalState.LISTENING, TransitionTrigger.TURN_ENDED)


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
