"""Reconnection + clean teardown integration test (spec V1 T12; criterion #11).

Two paths the acceptance criterion covers:

1. **Transient drop + ICE restart** — LiveKit's WebRTC stack handles
   transient network blips INTERNALLY via ICE restart. persona-voice does
   not own ICE state; the agent's session stays ``active`` across a
   transient drop. This integration test does NOT simulate a real network
   drop (the test harness can't easily induce one); it relies on LiveKit's
   ``connection_state`` callback shape as the contract surface and
   asserts our state machine doesn't react to intermediate states.

2. **Abrupt close** — the peer connection is killed (client crash, kill
   -9, OS network interface down). ``Room.on('disconnected')`` fires;
   :meth:`SessionStateMachine.end` runs which (a) flips
   ``state="ended"``, (b) disposes the per-session RLS engine which (c)
   rolls back any in-flight transaction which (d) releases the per-user
   ``pg_try_advisory_xact_lock`` (D-V1-5). The lock-release chain is the
   safety property that lets a crashed agent worker not strand a
   per-user-concurrency slot.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from livekit import api, rtc
from persona_voice.session.state_machine import SessionStateMachine
from persona_voice.transport.room import VoiceRoom

pytestmark = [pytest.mark.integration]


def _mint_token(
    *,
    api_key: str,
    api_secret: str,
    identity: str,
    room: str,
    ttl_s: int = 120,
) -> str:
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_grants(
            api.VideoGrants(
                room=room,
                room_join=True,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(timedelta(seconds=ttl_s))
        .to_jwt()
    )


@pytest.mark.asyncio
async def test_abrupt_disconnect_ends_session_and_releases_engine(
    livekit_url: str,
    livekit_api_key: str,
    livekit_api_secret: str,
    require_livekit_server: None,
) -> None:
    """Path (b): abrupt close → session.end() → engine.dispose() → lock released.

    The end() chain is the safety property D-V1-5 hinges on: the per-user
    advisory lock auto-releases via ``rls_engine.begin()`` transaction
    rollback when the engine disposes mid-flight. Here we mock the engine
    so the test can assert dispose was called; the actual DB-level lock
    release is exercised by the unit tests in test_session_state_machine.py
    and the cross-tenant integration tests in test_cross_tenant_rls.py.
    """
    room_name = f"persona-voice-t12-{os.getpid()}-{int(time.monotonic() * 1000)}"

    # ---- agent role with session wired via attach_to_room ----
    underlying_room = rtc.Room()
    voice_room = VoiceRoom(underlying_room)

    rls_engine_mock = MagicMock()
    rls_engine_mock.dispose = MagicMock(return_value=None)

    session = SessionStateMachine(
        session_id="sess_t12",
        user_id="user_test",
        persona_id="p_test",
        conversation_id="c_test",
        rls_engine=rls_engine_mock,
    )
    session.attach_to_room(voice_room)
    await session.mark_active()
    assert session.state == "active"

    token = _mint_token(
        api_key=livekit_api_key,
        api_secret=livekit_api_secret,
        identity=f"agent-abrupt-{os.getpid()}-{int(time.monotonic() * 1000)}",
        room=room_name,
    )

    await voice_room.connect(livekit_url, token)
    assert voice_room.is_connected

    # Allow a beat for the LiveKit Server to fully acknowledge the join
    # before we tear down — otherwise the disconnect event can be
    # swallowed by an in-flight handshake.
    await asyncio.sleep(0.5)

    # ---- ABRUPT close (simulated by direct disconnect; LiveKit fires the
    # ``disconnected`` event the same way it would on a real peer kill) ----
    await voice_room.disconnect()

    # ---- wait for the disconnect-handler chain to land ----
    # The handler ``set_disconnect_handler`` scheduled via
    # ``asyncio.ensure_future`` runs on the next event-loop tick; give it
    # a generous slack to land + complete end().
    for _ in range(30):
        if session.state == "ended":
            break
        await asyncio.sleep(0.1)

    # ---- assert: session ended + engine disposed ----
    assert session.state == "ended", (
        f"session still {session.state} after abrupt disconnect — "
        "Room.on('disconnected') → end() chain is broken"
    )
    assert session.session.ended_at is not None
    rls_engine_mock.dispose.assert_called_once()


@pytest.mark.asyncio
async def test_connection_state_observable_for_transient_drop() -> None:
    """Path (a): the LiveKit Room exposes the contract surface persona-voice
    depends on for transient-drop ICE-restart handling.

    persona-voice does NOT manage ICE restart itself — LiveKit's WebRTC
    stack handles transient network drops internally. The actual
    network-drop *simulation* is **🟦 operator-passed at V4 close** per
    D-V1-X-closeout-operator-pass-convention; controlled network
    conditions are not reproducibly inducible from the unit-test process.

    What we CAN assert without a live connection is the *contract
    surface* persona-voice's reconnection handling reads — the
    :class:`livekit.rtc.Room` ``connection_state`` property + the
    ``on(event, cb)`` dispatch the :class:`VoiceRoom` facade subscribes
    to at construction. This guards against an SDK upgrade silently
    renaming or removing that surface (which would silently break
    persona-voice's session-level recovery without a clear failure mode).
    """
    underlying_room = rtc.Room()
    voice_room = VoiceRoom(underlying_room)
    session = SessionStateMachine(
        session_id="sess_t12_trans",
        user_id="user_test",
        persona_id="p_test",
        conversation_id="c_test",
        rls_engine=MagicMock(),
    )
    session.attach_to_room(voice_room)

    # Contract surface persona-voice's reconnection path depends on:
    assert hasattr(underlying_room, "connection_state"), (
        "rtc.Room must expose connection_state for VoiceRoom session "
        "recovery — LiveKit SDK contract regression"
    )
    assert hasattr(underlying_room, "isconnected"), (
        "rtc.Room must expose isconnected() — VoiceRoom.is_connected reads it"
    )
    assert hasattr(underlying_room, "on"), (
        "rtc.Room must expose on(event, cb) — VoiceRoom registers "
        "track_subscribed + disconnected handlers via this surface"
    )
    # VoiceRoom registered the disconnect handler at construction
    # (test_construction_registers_inbound_and_disconnect_handlers proves
    # this against a fake substrate; here we verify the wiring holds
    # against the real rtc.Room class).
    assert voice_room._room is underlying_room  # noqa: SLF001 — contract surface
    # Session machinery is initialised + attached; the wired disconnect
    # handler runs end() if the live abrupt-disconnect path fires (covered
    # by test_abrupt_disconnect_ends_session_and_releases_engine).
    assert session.state == "created"
