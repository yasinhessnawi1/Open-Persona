"""LiveKit access-token minting (spec V1 T04, D-V1-3).

persona-voice acts as a trusted token issuer between the user's IdP-issued
JWT (verified via :func:`persona.auth.jwt_verifier.make_jwt_verifier`) and the
LiveKit Server's room-join JWT. The HTTP endpoint at
``POST /v1/voice/token`` verifies the user, checks persona ownership, and
calls this module to sign the LiveKit token that the client uses to join the
Room. The signing key (``LIVEKIT_API_SECRET``) never leaves the deployment.

The token's grants are tight: ``room_join=True`` for the per-session Room only,
``can_publish=True`` (user microphone), ``can_subscribe=True`` (persona's TTS
output). Room metadata carries ``persona_id`` and ``conversation_id`` so the
LiveKit Server forwards them to the agent worker when the participant connects
— persona-voice's agent worker reads metadata to bind the session to the right
persona context (D-V1-4) without an extra round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from livekit import api

__all__ = [
    "RoomAccessToken",
    "mint_room_access_token",
]


@dataclass(frozen=True)
class RoomAccessToken:
    """Result returned to the client by ``POST /v1/voice/token``.

    Frozen + ``__slots__``-equivalent (dataclass) — boundary type per the
    D-05-9 discipline. Carries the signed JWT, the Room name the client joins,
    and the LiveKit Server's WebSocket URL.
    """

    token: str
    room_name: str
    livekit_url: str


def _room_name_for_session(session_id: str) -> str:
    """Derive a LiveKit Room name from the session id.

    Deterministic so the agent worker can recompute the same Room name from
    the same session record. Prefixed so the LiveKit Server can apply a
    namespace policy if needed.
    """
    return f"persona:{session_id}"


def mint_room_access_token(
    *,
    api_key: str,
    api_secret: str,
    livekit_url: str,
    session_id: str,
    user_id: str,
    persona_id: str,
    conversation_id: str,
    ttl_s: int,
) -> RoomAccessToken:
    """Sign a LiveKit access token granting the user join access to one Room.

    The grants are scoped to ``room=<session_room_name>`` only — the token
    cannot join other Rooms, list Rooms, or create new ones. Identity is the
    persona-voice user id (Spec 08 JWT ``sub`` claim) so the LiveKit Server
    surfaces a stable participant identity for logging + the V4 lifecycle
    hooks (T06 / T07).
    """
    room_name = _room_name_for_session(session_id)
    metadata = (
        f'{{"persona_id":"{persona_id}",'
        f'"conversation_id":"{conversation_id}",'
        f'"session_id":"{session_id}"}}'
    )
    grants = api.VideoGrants(
        room=room_name,
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(user_id)
        .with_grants(grants)
        .with_metadata(metadata)
        .with_ttl(timedelta(seconds=ttl_s))
        .to_jwt()
    )
    return RoomAccessToken(token=token, room_name=room_name, livekit_url=livekit_url)
