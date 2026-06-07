"""Unit tests for the persona-voice HTTP app (spec V1 T04).

Mounts the FastAPI app with a fake JWT verifier and a fake ``owns_persona``
override so the suite needs neither Clerk nor a database. Tests cover the
auth seam, the ownership check, the happy-path response shape, and the
failure modes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from persona.auth.jwt_verifier import AuthenticatedUser
from persona.errors import AuthenticationError
from persona_voice.config import VoiceConfig
from persona_voice.http.app import build_app
from pydantic import SecretStr


def _build_test_client(*, owns_persona_result: bool = True) -> TestClient:
    cfg = VoiceConfig(
        livekit_url="ws://localhost:7880",
        livekit_api_key=SecretStr("lk_key_test"),
        livekit_api_secret=SecretStr("very-very-long-test-secret-for-hs256-signing"),
        jwt_secret=SecretStr("s3cret"),
        jwt_algorithms="HS256",
    )
    app = build_app(cfg)

    async def _fake_verify(token: str) -> AuthenticatedUser:
        if token == "good":
            return AuthenticatedUser(id="user_test", email="a@x.test")
        raise AuthenticationError("bad token")

    app.state.verify_token = _fake_verify

    def _owns(*, persona_id: str, user_id: str) -> bool:  # noqa: ARG001
        return owns_persona_result

    app.state.owns_persona = _owns
    return TestClient(app)


def test_token_endpoint_requires_bearer() -> None:
    client = _build_test_client()
    resp = client.post(
        "/v1/voice/token",
        json={"persona_id": "p1", "conversation_id": "c1"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "authentication_error"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_token_endpoint_rejects_invalid_bearer() -> None:
    client = _build_test_client()
    resp = client.post(
        "/v1/voice/token",
        headers={"Authorization": "Bearer wrong"},
        json={"persona_id": "p1", "conversation_id": "c1"},
    )
    assert resp.status_code == 401


def test_token_endpoint_404_when_persona_not_owned() -> None:
    client = _build_test_client(owns_persona_result=False)
    resp = client.post(
        "/v1/voice/token",
        headers={"Authorization": "Bearer good"},
        json={"persona_id": "p_other_tenant", "conversation_id": "c1"},
    )
    # RLS-shape: never leaks whether the persona exists for another tenant.
    assert resp.status_code == 404


def test_token_endpoint_happy_path_returns_signed_token() -> None:
    client = _build_test_client()
    resp = client.post(
        "/v1/voice/token",
        headers={"Authorization": "Bearer good"},
        json={"persona_id": "p_astrid", "conversation_id": "c_42"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"token", "room_name", "livekit_url"}
    assert body["livekit_url"] == "ws://localhost:7880"
    assert body["room_name"].startswith("persona:")
    # The minted token decodes against our test signing secret.
    decoded = jwt.decode(
        body["token"],
        "very-very-long-test-secret-for-hs256-signing",
        algorithms=["HS256"],
        options={"verify_aud": False},
    )
    assert decoded["sub"] == "user_test"
    assert decoded["video"]["room"] == body["room_name"]
    assert decoded["video"]["roomJoin"] is True


def test_token_endpoint_rejects_body_with_extra_fields() -> None:
    """The body schema has ``extra='forbid'`` so unknown fields are rejected
    (defense-in-depth: keeps clients from accidentally smuggling state).
    """
    client = _build_test_client()
    resp = client.post(
        "/v1/voice/token",
        headers={"Authorization": "Bearer good"},
        json={"persona_id": "p", "conversation_id": "c", "owner_id": "spoofed"},
    )
    assert resp.status_code == 422


def test_voice_config_reads_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_VOICE_LIVEKIT_URL", "wss://lk.test")
    monkeypatch.setenv("PERSONA_VOICE_LIVEKIT_API_KEY", "ak_env")
    monkeypatch.setenv("PERSONA_VOICE_LIVEKIT_API_SECRET", "as_env_super_long_secret")
    monkeypatch.setenv("PERSONA_VOICE_JWT_SECRET", "env_secret")
    monkeypatch.setenv("PERSONA_VOICE_JWT_ALGORITHMS", "HS256,RS256")
    cfg = VoiceConfig()
    assert cfg.livekit_url == "wss://lk.test"
    assert cfg.livekit_api_key.get_secret_value() == "ak_env"
    assert cfg.livekit_api_secret.get_secret_value() == "as_env_super_long_secret"
    assert cfg.jwt_secret is not None
    assert cfg.jwt_secret.get_secret_value() == "env_secret"
    # Comma-separated list parsed by the computed property.
    assert cfg.jwt_algorithms_list == ["HS256", "RS256"]
