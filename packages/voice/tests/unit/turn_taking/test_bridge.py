"""Unit tests for the T06 bridge — SessionEventBridge + CompositeStateListener.

LoopTurnActions cancellation is exercised end-to-end in
``tests/integration/test_v4_orchestration.py``; here we cover the pure mapping
+ fanout logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from persona_voice.session.state_machine import SessionLifecycleEvent, SessionStateMachine
from persona_voice.turn_taking.bridge import CompositeStateListener, SessionEventBridge
from persona_voice.turn_taking.states import (
    ConversationalState,
    ConversationalTransition,
    TransitionTrigger,
)

pytestmark = pytest.mark.asyncio

_AT = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


def _transition(
    from_state: ConversationalState,
    to_state: ConversationalState,
    trigger: TransitionTrigger,
) -> ConversationalTransition:
    return ConversationalTransition(
        from_state=from_state, to_state=to_state, trigger=trigger, at=_AT
    )


def _session_capturing(events: list[SessionLifecycleEvent]) -> SessionStateMachine:
    async def _on_event(ev: SessionLifecycleEvent, _s: object) -> None:
        events.append(ev)

    return SessionStateMachine(
        session_id="s",
        user_id="u",
        persona_id="p",
        conversation_id="c",
        rls_engine=MagicMock(),
        on_event=_on_event,
    )


async def test_entering_user_speaking_emits_user_started() -> None:
    events: list[SessionLifecycleEvent] = []
    bridge = SessionEventBridge(_session_capturing(events))
    await bridge.on_state_changed(
        _transition(
            ConversationalState.LISTENING,
            ConversationalState.USER_SPEAKING,
            TransitionTrigger.USER_SPEECH_STARTED,
        )
    )
    assert events == [SessionLifecycleEvent.USER_STARTED_SPEAKING]


async def test_barge_in_to_user_speaking_emits_user_started() -> None:
    events: list[SessionLifecycleEvent] = []
    bridge = SessionEventBridge(_session_capturing(events))
    await bridge.on_state_changed(
        _transition(
            ConversationalState.PERSONA_SPEAKING,
            ConversationalState.USER_SPEAKING,
            TransitionTrigger.BARGE_IN,
        )
    )
    assert events == [SessionLifecycleEvent.USER_STARTED_SPEAKING]


async def test_leaving_user_speaking_emits_user_stopped() -> None:
    events: list[SessionLifecycleEvent] = []
    bridge = SessionEventBridge(_session_capturing(events))
    await bridge.on_state_changed(
        _transition(
            ConversationalState.USER_SPEAKING,
            ConversationalState.PROCESSING,
            TransitionTrigger.TURN_ENDED,
        )
    )
    assert events == [SessionLifecycleEvent.USER_STOPPED_SPEAKING]


async def test_agent_transitions_do_not_emit_user_events() -> None:
    """The bridge does NOT re-emit agent events (the loop owns those)."""
    events: list[SessionLifecycleEvent] = []
    bridge = SessionEventBridge(_session_capturing(events))
    await bridge.on_state_changed(
        _transition(
            ConversationalState.PROCESSING,
            ConversationalState.PERSONA_SPEAKING,
            TransitionTrigger.MODEL_FIRST_AUDIO,
        )
    )
    await bridge.on_state_changed(
        _transition(
            ConversationalState.PERSONA_SPEAKING,
            ConversationalState.LISTENING,
            TransitionTrigger.PERSONA_FINISHED,
        )
    )
    assert events == []


async def test_composite_fans_out_to_all_listeners() -> None:
    seen_a: list[ConversationalTransition] = []
    seen_b: list[ConversationalTransition] = []

    class _L:
        def __init__(self, sink: list[ConversationalTransition]) -> None:
            self._sink = sink

        async def on_state_changed(self, transition: ConversationalTransition) -> None:
            self._sink.append(transition)

    composite = CompositeStateListener([_L(seen_a), _L(seen_b)])
    t = _transition(
        ConversationalState.LISTENING,
        ConversationalState.USER_SPEAKING,
        TransitionTrigger.USER_SPEECH_STARTED,
    )
    await composite.on_state_changed(t)
    assert seen_a == [t]
    assert seen_b == [t]
