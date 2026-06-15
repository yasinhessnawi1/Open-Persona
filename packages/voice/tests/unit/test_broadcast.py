"""Unit tests for the V6 A1 data-channel broadcaster (spec V6).

Covers the wire envelope (D-V6-E1/E2), segment-id mutate-and-replace semantics
(D-V6-2), the room-scoped owner-only delivery property (the security property
the user flagged for verification), and the never-raise listener contract.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from persona_voice.loop.streaming import Transcript
from persona_voice.transport.broadcast import BROADCAST_TOPIC, DataChannelBroadcaster
from persona_voice.turn_taking.states import (
    ConversationalState,
    ConversationalTransition,
    TransitionTrigger,
)

pytestmark = [pytest.mark.asyncio]


class _CapturingRoom:
    """Records every publish_data call — the ONLY sink the broadcaster targets."""

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self._raises = raises

    async def publish_data(
        self, payload: bytes, *, reliable: bool = True, topic: str | None = None
    ) -> None:
        if self._raises:
            msg = "transport down"
            raise RuntimeError(msg)
        self.calls.append(
            {"payload": json.loads(payload.decode("utf-8")), "reliable": reliable, "topic": topic}
        )


def _transition(
    to_state: ConversationalState, trigger: TransitionTrigger
) -> ConversationalTransition:
    return ConversationalTransition(
        from_state=ConversationalState.PERSONA_SPEAKING,
        to_state=to_state,
        trigger=trigger,
        at=datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC),
    )


async def test_state_frame_is_reliable_on_topic_and_carries_the_transition() -> None:
    room = _CapturingRoom()
    bc = DataChannelBroadcaster(room)  # type: ignore[arg-type]

    await bc.on_state_changed(
        _transition(ConversationalState.USER_SPEAKING, TransitionTrigger.BARGE_IN)
    )

    assert len(room.calls) == 1
    call = room.calls[0]
    assert call["reliable"] is True
    assert call["topic"] == BROADCAST_TOPIC
    frame = call["payload"]
    assert frame == {
        "type": "state",
        "from_state": "persona_speaking",
        "to_state": "user_speaking",
        "trigger": "barge_in",
        "at": "2026-06-15T12:00:00+00:00",
    }


async def test_user_caption_segment_id_advances_only_after_a_final() -> None:
    room = _CapturingRoom()
    bc = DataChannelBroadcaster(room)  # type: ignore[arg-type]

    await bc.on_user_transcript(Transcript(is_final=False, text="hel", confidence=0.5))
    await bc.on_user_transcript(Transcript(is_final=False, text="hello", confidence=0.6))
    await bc.on_user_transcript(Transcript(is_final=True, text="hello there", confidence=0.9))
    await bc.on_user_transcript(Transcript(is_final=False, text="next", confidence=0.5))

    segs = [c["payload"]["segment_id"] for c in room.calls]  # type: ignore[index]
    speakers = {c["payload"]["speaker"] for c in room.calls}  # type: ignore[index]
    assert speakers == {"user"}
    # Partials + final of one utterance share u0; the next utterance starts u1.
    assert segs == ["u0", "u0", "u0", "u1"]
    finals = [c["payload"]["is_final"] for c in room.calls]  # type: ignore[index]
    assert finals == [False, False, True, False]


async def test_persona_caption_is_verbatim_and_segments_like_user() -> None:
    room = _CapturingRoom()
    bc = DataChannelBroadcaster(room)  # type: ignore[arg-type]

    await bc.on_persona_text("Tenants", is_final=False)
    await bc.on_persona_text("Tenants have", is_final=False)
    await bc.on_persona_text("Tenants have rights.", is_final=True)
    await bc.on_persona_text("New", is_final=False)

    payloads = [c["payload"] for c in room.calls]
    assert [p["speaker"] for p in payloads] == ["persona"] * 4  # type: ignore[index]
    assert [p["segment_id"] for p in payloads] == ["p0", "p0", "p0", "p1"]  # type: ignore[index]
    assert payloads[2]["text"] == "Tenants have rights."  # type: ignore[index]


async def test_owner_only_delivery_targets_only_its_own_room_and_never_cross_targets() -> None:
    # The security property (verified, not asserted): the broadcaster publishes
    # ONLY via its injected room and NEVER passes a destination_identities — so
    # LiveKit confines delivery to this per-session Room's participants (the
    # call's own owner). Two broadcasters on two rooms never cross-publish.
    room_a = _CapturingRoom()
    room_b = _CapturingRoom()
    bc_a = DataChannelBroadcaster(room_a)  # type: ignore[arg-type]
    bc_b = DataChannelBroadcaster(room_b)  # type: ignore[arg-type]

    await bc_a.on_persona_text("for A only", is_final=True)
    await bc_b.on_persona_text("for B only", is_final=True)

    # Each broadcaster published exactly once, ONLY into its own room — no
    # cross-call leakage.
    assert [c["payload"]["text"] for c in room_a.calls] == ["for A only"]  # type: ignore[index]
    assert [c["payload"]["text"] for c in room_b.calls] == ["for B only"]  # type: ignore[index]
    # publish_data is called with NO destination_identities kwarg — the
    # _CapturingRoom signature would reject one, so a passing call proves the
    # broadcaster only ever broadcasts to the room (never targets an identity).
    assert set(room_a.calls[0].keys()) == {"payload", "reliable", "topic"}


async def test_publish_errors_are_swallowed_never_raised_into_the_turn() -> None:
    room = _CapturingRoom(raises=True)
    bc = DataChannelBroadcaster(room)  # type: ignore[arg-type]

    # None of the three seam methods may raise — a transient data-channel error
    # is cosmetic; raising would wedge the orchestrator's turn cycle.
    await bc.on_state_changed(
        _transition(ConversationalState.LISTENING, TransitionTrigger.PERSONA_FINISHED)
    )
    await bc.on_user_transcript(Transcript(is_final=True, text="x", confidence=1.0))
    await bc.on_persona_text("y", is_final=True)
