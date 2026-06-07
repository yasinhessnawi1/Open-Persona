"""Cross-tenant RLS sweep (spec V1 T11; criteria #6 + #7).

The acceptance contract: a voice session can only be opened for a persona
the JWT-authed user owns. Cross-tenant access fails closed at every layer:

1. **Token-endpoint ownership check** (defense-in-depth). The
   ``/v1/voice/token`` endpoint runs ``SELECT 1 FROM personas WHERE id=:pid
   AND owner_id=:uid`` before minting the LiveKit token. A 404 surfaces
   whether the persona is missing OR owned by another tenant — same shape
   persona-api uses to avoid leaking persona existence across tenants.

2. **Session-bound RLS engine** (D-V1-X-rls-engine-shape; T06). The engine
   ``make_session_rls_engine`` returns has a checkout listener that runs
   ``SELECT set_config('app.current_user_id', :uid, false)`` so every
   subsequent ``memory_chunks`` query the audio loop issues is RLS-scoped
   to the call's user. Even if Layer 1 were bypassed (it isn't), this
   layer sees zero rows for the wrong tenant — RLS is **structural**, not
   disciplinary (the D-08-1 invariant).

The test exercises both layers against the running persona-pg (Spec 07's
production schema with the ``persona_app`` non-superuser role + RLS
policies applied).
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from persona_voice.config import VoiceConfig
from persona_voice.http.app import build_app
from persona_voice.session.state_machine import make_session_rls_engine
from pydantic import SecretStr
from sqlalchemy import create_engine, text

pytestmark = [pytest.mark.integration]


# ---------- fixtures -------------------------------------------------------


def _superuser_url() -> str:
    return os.environ.get(
        "PERSONA_VOICE_TEST_DATABASE_URL",
        "postgresql+psycopg://persona:persona@localhost:5436/persona",
    )


def _app_url() -> str | None:
    """The same DB as the superuser URL but accessed via the ``persona_app``
    non-superuser role so RLS actually fires (RLS bypasses superusers per
    D-07-5). Returns ``None`` if the URL is not configured — tests that
    require RLS to fire then skip cleanly (same convention as the
    persona-api integration suite).
    """
    return os.environ.get(
        "APP_DATABASE_URL",
        os.environ.get("PERSONA_VOICE_TEST_APP_DATABASE_URL"),
    )


@pytest.fixture(scope="module")
def require_postgres() -> None:
    """Skip the module if persona-pg isn't reachable on the configured URL."""
    try:
        engine = create_engine(_superuser_url(), pool_size=1)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception as exc:
        pytest.skip(f"persona-pg not reachable for T11: {exc}")


@pytest.fixture
def tenant_pair(require_postgres: None) -> dict[str, str]:
    """Insert two users + one persona owned by user_a; yield the ids; clean up.

    Uses the superuser role so the inserts succeed regardless of RLS — RLS
    only governs *queries* from non-superuser roles, which is what the
    actual cross-tenant tests below assert.
    """
    user_a_id = f"u_a_{uuid.uuid4().hex[:8]}"
    user_b_id = f"u_b_{uuid.uuid4().hex[:8]}"
    persona_id = f"p_a_{uuid.uuid4().hex[:8]}"

    engine = create_engine(_superuser_url(), pool_size=1)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email) VALUES (:id, :email) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": user_a_id, "email": f"{user_a_id}@x.test"},
            )
            conn.execute(
                text(
                    "INSERT INTO users (id, email) VALUES (:id, :email) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": user_b_id, "email": f"{user_b_id}@x.test"},
            )
            conn.execute(
                text(
                    "INSERT INTO personas (id, owner_id, yaml, schema_version) "
                    "VALUES (:id, :owner, :yaml, :ver)"
                ),
                {
                    "id": persona_id,
                    "owner": user_a_id,
                    "yaml": (
                        "schema_version: '1.0'\n"
                        f"persona_id: {persona_id}\n"
                        "identity:\n  name: Astrid\n  role: tester\n"
                    ),
                    "ver": "1.0",
                },
            )
        yield {"user_a": user_a_id, "user_b": user_b_id, "persona_id": persona_id}
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM personas WHERE id = :id"), {"id": persona_id})
            conn.execute(
                text("DELETE FROM users WHERE id IN (:a, :b)"),
                {"a": user_a_id, "b": user_b_id},
            )
        engine.dispose()


def _mint_jwt(user_id: str, secret: str) -> str:
    """Mint a test JWT signed with the same HS256 secret persona-voice
    is configured with — mirrors the persona-api fake-JWT pattern used by
    spec 08 acceptance #14."""
    return jwt.encode(
        {"sub": user_id, "email": f"{user_id}@x.test", "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )


def _voice_app_with_real_db() -> TestClient:
    """Build the persona-voice FastAPI app pointed at the live persona-pg
    via the superuser role (the token-endpoint ownership check needs to
    READ from ``personas``; RLS isn't the layer being tested at this
    endpoint, the explicit WHERE filter is)."""
    cfg = VoiceConfig(
        livekit_url="ws://localhost:7880",
        livekit_api_key=SecretStr("devkey"),
        livekit_api_secret=SecretStr("secret_at_least_32_chars_for_hs256_signing_xx"),
        jwt_secret=SecretStr("t11_test_secret_at_least_32_chars_for_hs256_signing"),
        jwt_algorithms="HS256",
        database_url=_superuser_url(),
    )
    return TestClient(build_app(cfg))


# ---------- Layer 1: token-endpoint ownership check -----------------------


def test_cross_tenant_token_request_is_404(
    tenant_pair: dict[str, str],
) -> None:
    """Tenant B requests a voice token for tenant A's persona → 404.

    This is the BINARY acceptance criterion #6 + #7 for cross-tenant
    access: the endpoint must NEVER mint a LiveKit token bound to a
    persona the JWT user doesn't own. 404 (not 403) keeps persona
    existence opaque across tenants — same shape persona-api uses for
    the conversation/run endpoints.
    """
    client = _voice_app_with_real_db()
    user_b_jwt = _mint_jwt(
        tenant_pair["user_b"],
        secret="t11_test_secret_at_least_32_chars_for_hs256_signing",
    )
    resp = client.post(
        "/v1/voice/token",
        headers={"Authorization": f"Bearer {user_b_jwt}"},
        json={
            "persona_id": tenant_pair["persona_id"],
            "conversation_id": f"c_{uuid.uuid4().hex[:8]}",
        },
    )
    assert resp.status_code == 404, resp.text


def test_owner_can_get_token_for_their_own_persona(
    tenant_pair: dict[str, str],
) -> None:
    """Positive control: tenant A requesting tenant A's persona → 200."""
    client = _voice_app_with_real_db()
    user_a_jwt = _mint_jwt(
        tenant_pair["user_a"],
        secret="t11_test_secret_at_least_32_chars_for_hs256_signing",
    )
    resp = client.post(
        "/v1/voice/token",
        headers={"Authorization": f"Bearer {user_a_jwt}"},
        json={
            "persona_id": tenant_pair["persona_id"],
            "conversation_id": f"c_{uuid.uuid4().hex[:8]}",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"]
    assert body["room_name"].startswith("persona:")


# ---------- Layer 2: session-bound RLS engine -----------------------------


def test_session_rls_engine_scopes_personas_query_to_user(
    tenant_pair: dict[str, str],
) -> None:
    """Layer 2: the session-bound RLS engine (T06) sees only the call's
    user's rows. Tenant B's engine queries the personas table → cannot
    see tenant A's persona row, even though the row physically exists.

    Uses the ``persona_app`` non-superuser role (RLS bypasses superusers
    per D-07-5). The engine's ``checkout`` listener sets
    ``app.current_user_id`` from the user_id baked into the factory; the
    personas table's RLS policy filters to ``owner_id =
    current_setting('app.current_user_id')``.
    """
    app_url = _app_url()
    if app_url is None:
        pytest.skip(
            "APP_DATABASE_URL not set — Layer 2 RLS engine test requires "
            "a persona_app non-superuser DSN (same convention as persona-api "
            "integration suite). Set APP_DATABASE_URL=postgresql+psycopg://"
            "persona_app:<pwd>@host:port/persona to enable."
        )
    try:
        engine_a = make_session_rls_engine(app_url, user_id=tenant_pair["user_a"])
        engine_b = make_session_rls_engine(app_url, user_id=tenant_pair["user_b"])
    except Exception as exc:
        pytest.skip(f"persona_app role not reachable at {app_url}: {exc}")
    try:
        with engine_a.connect() as conn:
            row_a = conn.execute(
                text("SELECT 1 FROM personas WHERE id = :id"),
                {"id": tenant_pair["persona_id"]},
            ).first()
            assert row_a is not None, (
                "tenant A must see their own persona under the session RLS engine"
            )

        with engine_b.connect() as conn:
            row_b = conn.execute(
                text("SELECT 1 FROM personas WHERE id = :id"),
                {"id": tenant_pair["persona_id"]},
            ).first()
            assert row_b is None, (
                "tenant B must NOT see tenant A's persona under the session "
                "RLS engine; defense-in-depth is broken"
            )
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_unscoped_engine_sees_nothing_fail_closed(
    tenant_pair: dict[str, str],
) -> None:
    """If the session RLS engine were built with an empty user_id (the
    fail-closed case D-08-1 calls out), no rows are visible — RLS scopes
    to ``app.current_user_id = ''`` which matches no policy row.

    This guards against a regression where ``make_session_rls_engine``
    silently accepted an empty string and the operator never noticed
    until cross-tenant data leaked.
    """
    app_url = _app_url()
    if app_url is None:
        pytest.skip("APP_DATABASE_URL not set — see Layer 2 test docstring.")
    try:
        engine = make_session_rls_engine(app_url, user_id="")
    except Exception as exc:
        pytest.skip(f"persona_app role not reachable: {exc}")
    try:
        with engine.connect() as conn:
            # Even a query that wouldn't reasonably leak — count all
            # personas — must see zero rows because the policy filter
            # rejects an empty current_user_id.
            count = conn.execute(text("SELECT count(*) FROM personas")).scalar()
            assert count == 0, (
                f"unscoped session engine saw {count} personas; fail-closed invariant violated"
            )
    finally:
        engine.dispose()
