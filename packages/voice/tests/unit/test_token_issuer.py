"""Unit tests for the LiveKit access-token issuer (spec V1 T04).

Verifies the JWT shape persona-voice signs — the LiveKit Server enforces
``room_join`` + ``room`` grants, identity, expiry, and metadata; this suite
guards the signing-side contract that produces them.
"""

from __future__ import annotations

import json

from jose import jwt
from persona_voice.tokens.issuer import (
    RoomAccessToken,
    _room_name_for_session,
    mint_room_access_token,
)


def _decode_unverified(token: str) -> dict[str, object]:
    """Decode a JWT without verification — we're inspecting persona-voice's
    own output, not a third party's.
    """
    # python-jose decode requires the secret/key; for inspection we use the
    # raw split + b64 decode that matches the JWS structure.
    import base64

    _hdr, body, _sig = token.split(".")
    padding = "=" * (-len(body) % 4)
    return dict(json.loads(base64.urlsafe_b64decode(body + padding)))


def test_room_name_is_deterministic_per_session_id() -> None:
    assert _room_name_for_session("sess_a") == "persona:sess_a"
    assert _room_name_for_session("abc123") == "persona:abc123"
    # Distinct session ids produce distinct room names.
    assert _room_name_for_session("a") != _room_name_for_session("b")


def test_mint_room_access_token_returns_signed_jwt_with_grants() -> None:
    out = mint_room_access_token(
        api_key="lk_key_test",
        api_secret="lk_secret_test_long_enough_for_signing_in_hs256",
        livekit_url="ws://localhost:7880",
        session_id="sess_xyz",
        user_id="user_abc",
        persona_id="p_astrid",
        conversation_id="c_chat",
        ttl_s=600,
    )
    assert isinstance(out, RoomAccessToken)
    assert out.room_name == "persona:sess_xyz"
    assert out.livekit_url == "ws://localhost:7880"

    payload = _decode_unverified(out.token)
    # LiveKit AccessToken stamps `sub` = identity and `video` = grants.
    assert payload["sub"] == "user_abc"
    video = payload["video"]
    assert isinstance(video, dict)
    assert video["room"] == "persona:sess_xyz"
    assert video["roomJoin"] is True
    # can_publish / can_subscribe are True by default in our grant set.
    assert video.get("canPublish", True) is True
    assert video.get("canSubscribe", True) is True


def test_metadata_carries_persona_and_conversation_ids() -> None:
    out = mint_room_access_token(
        api_key="k",
        api_secret="very-very-long-test-secret-for-hs256-signing",
        livekit_url="ws://localhost:7880",
        session_id="sess_meta",
        user_id="u",
        persona_id="p_legal",
        conversation_id="c_42",
        ttl_s=600,
    )
    payload = _decode_unverified(out.token)
    metadata_raw = payload.get("metadata")
    assert isinstance(metadata_raw, str)
    metadata = json.loads(metadata_raw)
    assert metadata["persona_id"] == "p_legal"
    assert metadata["conversation_id"] == "c_42"
    assert metadata["session_id"] == "sess_meta"


def test_ttl_is_signed_into_exp_claim() -> None:
    import time

    before = int(time.time())
    out = mint_room_access_token(
        api_key="k",
        api_secret="very-very-long-test-secret-for-hs256-signing",
        livekit_url="ws://localhost:7880",
        session_id="sess_ttl",
        user_id="u",
        persona_id="p",
        conversation_id="c",
        ttl_s=120,
    )
    after = int(time.time())
    payload = _decode_unverified(out.token)
    exp = payload["exp"]
    assert isinstance(exp, int)
    # exp lies within [before+120, after+120 + small slack].
    assert before + 120 <= exp <= after + 121


def test_token_verifies_with_the_signing_secret() -> None:
    """An honest round-trip: a token persona-voice signs with secret S decodes
    cleanly against S. (Production decoding happens inside LiveKit Server with
    the same secret.)
    """
    secret = "very-very-long-test-secret-for-hs256-signing"
    out = mint_room_access_token(
        api_key="k",
        api_secret=secret,
        livekit_url="ws://localhost:7880",
        session_id="sess_verify",
        user_id="u",
        persona_id="p",
        conversation_id="c",
        ttl_s=600,
    )
    decoded = jwt.decode(out.token, secret, algorithms=["HS256"], options={"verify_aud": False})
    assert decoded["sub"] == "u"
