"""persona-voice HTTP app — the ``POST /v1/voice/token`` endpoint (spec V1 T04).

The endpoint is the **only** HTTP route the voice service exposes at v0.1.
LiveKit Server handles all WebRTC signaling, peer connections, and media
transport internally (D-V1-1 branch (A) + D-V1-3); persona-voice's job is to
mint a Room access token after verifying:

1. The caller's IdP JWT is valid (via the extracted ``make_jwt_verifier``).
2. The caller owns the requested persona (ownership check — defense-in-depth
   on top of the session-bound RLS engine T06 adds).

A passing call returns ``{token, room_name, livekit_url}`` — the client uses
these to join the LiveKit Room directly. Failures fail-closed: missing or
invalid JWT → 401; persona not visible to the caller → 404 (RLS-shape, never
leaks whether the persona exists for another tenant).

Ownership check is intentionally minimal at v0.1: a single ``SELECT`` against
``personas WHERE id = :pid AND owner_id = :uid``. The RLS-scoped engine T06
ships for the audio loop is a different concern (per-session lifecycle vs.
per-request). When persona-voice grows additional HTTP routes (post-V1), the
two patterns may consolidate.
"""

from __future__ import annotations

import uuid

# NOTE: `Request`, `Awaitable`, `Callable` are RUNTIME imports (NOT under
# TYPE_CHECKING) because FastAPI resolves dependency / route signatures via
# ``get_type_hints`` at startup — with ``from __future__ import annotations``
# every annotation is a string, so every name in a dependency's signature must
# be importable at runtime or FastAPI mis-reads the params (e.g. treating
# ``request: Request`` as a query parameter). Same pattern as
# persona-api ``auth/deps.py``.
from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from persona.auth.jwt_verifier import AuthenticatedUser, make_jwt_verifier
from persona.credits import require_credits as _require_credits_core
from persona.errors import AuthenticationError, CreditsExhaustedError
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, text

from persona_voice.config import VoiceConfig
from persona_voice.tokens.issuer import RoomAccessToken, mint_room_access_token

__all__ = ["build_app", "get_voice_config"]


class TokenRequest(BaseModel):
    """Body for ``POST /v1/voice/token``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona_id: str
    conversation_id: str


class TokenResponse(BaseModel):
    """Result returned to the client."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str
    room_name: str
    livekit_url: str


def get_voice_config(request: Request) -> VoiceConfig:
    """Provide the active :class:`VoiceConfig` (overridable in tests)."""
    cfg = getattr(request.app.state, "voice_config", None)
    if cfg is None:
        msg = "voice_config not configured on app.state"
        raise RuntimeError(msg)
    assert isinstance(cfg, VoiceConfig)
    return cfg


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthenticationError("missing or malformed Authorization header")
    token = header.removeprefix("Bearer ").strip()
    if not token:
        raise AuthenticationError("empty bearer token")
    return token


def get_verify_token(request: Request) -> Callable[[str], Awaitable[AuthenticatedUser]]:
    """Active token verifier — overridable on ``app.state.verify_token`` for tests."""
    verifier = getattr(request.app.state, "verify_token", None)
    if verifier is not None:
        return verifier  # type: ignore[no-any-return]
    cfg = get_voice_config(request)
    return make_jwt_verifier(cfg)


async def get_current_user(
    request: Request,
    verify: Callable[[str], Awaitable[AuthenticatedUser]] = Depends(get_verify_token),
) -> AuthenticatedUser:
    """Authenticate the request; return the user or raise ``AuthenticationError``."""
    return await verify(_bearer_token(request))


def _require_credits(request: Request, *, user_id: str) -> None:
    """Pre-flight credit gate for ``POST /v1/voice/token`` (D-19-X-voice-token-credit-gate).

    Mirrors the persona-api chat 402 contract (D-11-12): raises
    :class:`CreditsExhaustedError` when ``balance <= 0`` so the LiveKit Room
    token is never minted for a user out of credits. The check runs at token
    issue (call-start); per-turn deductions during the call are a separate
    concern. Tests can override via ``app.state.require_credits`` to skip the
    DB hop entirely (same pattern as ``owns_persona``).
    """
    override = getattr(request.app.state, "require_credits", None)
    if override is not None:
        override(user_id=user_id)
        return
    engine = getattr(request.app.state, "ownership_engine", None)
    if engine is None:
        msg = "ownership_engine not configured on app.state"
        raise RuntimeError(msg)
    _require_credits_core(rls_engine=engine, user_id=user_id)


def _check_persona_ownership(
    request: Request,
    *,
    persona_id: str,
    user_id: str,
) -> None:
    """Verify the user owns ``persona_id`` against the personas table.

    Raises ``HTTPException(404)`` if the persona is not visible to the user —
    the same shape persona-api uses to avoid leaking persona existence across
    tenants. In tests the app state can expose an ``owns_persona`` override
    so the DB hop is skipped entirely.
    """
    override = getattr(request.app.state, "owns_persona", None)
    if override is not None:
        if not override(persona_id=persona_id, user_id=user_id):
            raise HTTPException(status_code=404, detail="persona not found")
        return
    engine = getattr(request.app.state, "ownership_engine", None)
    if engine is None:
        msg = "ownership_engine not configured on app.state"
        raise RuntimeError(msg)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM personas WHERE id = :pid AND owner_id = :uid"),
            {"pid": persona_id, "uid": user_id},
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="persona not found")


def build_app(config: VoiceConfig) -> FastAPI:
    """Build the persona-voice FastAPI app.

    The app holds the active :class:`VoiceConfig` on ``app.state.voice_config``.
    Production callers also attach an ``ownership_engine`` (a SQLAlchemy
    Engine bound to the ``persona_app`` RLS-scoped role) before serving
    requests. Tests can override ``owns_persona`` instead to skip the DB hop.
    """
    app = FastAPI(title="persona-voice", version="0.1.0")
    app.state.voice_config = config
    if config.database_url:
        app.state.ownership_engine = create_engine(config.database_url, pool_size=1)

    # Spec V6 A0 (D-V6-X-agent-worker) — the dev/operator-pass-grade in-process
    # agent launcher. When enabled, the token endpoint spawns the agent that
    # joins the call's Room and becomes the persona. Default-off keeps the
    # token-only deployment + every existing test unaffected.
    app.state.agent_launcher = None
    if config.agent_inprocess:
        from persona_voice.agent import InProcessAgentLauncher

        launcher = InProcessAgentLauncher(config)
        app.state.agent_launcher = launcher

        @app.on_event("shutdown")
        async def _close_agent_launcher() -> None:
            await launcher.aclose()

    @app.exception_handler(AuthenticationError)
    async def _auth_error_handler(_req: Request, exc: AuthenticationError) -> object:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=401,
            content={"error": "authentication_error", "detail": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(CreditsExhaustedError)
    async def _credits_error_handler(_req: Request, exc: CreditsExhaustedError) -> object:
        # Mirrors the persona-api 402 contract (D-11-12) so the web client's
        # existing credits_exhausted handling works for voice too.
        from fastapi.responses import JSONResponse

        payload: dict[str, object] = {
            "error": "credits_exhausted",
            "detail": exc.message or "insufficient credits",
        }
        if exc.context:
            payload["context"] = exc.context
        return JSONResponse(status_code=402, content=payload)

    @app.post("/v1/voice/token", response_model=TokenResponse)
    async def issue_voice_token(
        body: TokenRequest,
        request: Request,
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> TokenResponse:
        _check_persona_ownership(request, persona_id=body.persona_id, user_id=user.id)
        _require_credits(request, user_id=user.id)
        cfg = get_voice_config(request)
        session_id = uuid.uuid4().hex
        token: RoomAccessToken = mint_room_access_token(
            api_key=cfg.livekit_api_key.get_secret_value(),
            api_secret=cfg.livekit_api_secret.get_secret_value(),
            livekit_url=cfg.livekit_url,
            session_id=session_id,
            user_id=user.id,
            persona_id=body.persona_id,
            conversation_id=body.conversation_id,
            ttl_s=cfg.livekit_token_ttl_s,
        )
        # Spec V6 A0 — launch the agent into the call's Room (dev path only;
        # default-off). The user joins ``room_name``; the agent joins the same
        # Room and becomes the persona. Fire-and-forget — a failed launch never
        # blocks the token response (the launcher catches + logs).
        launcher = getattr(request.app.state, "agent_launcher", None)
        if launcher is not None:
            launcher.launch(
                session_id=session_id,
                user_id=user.id,
                persona_id=body.persona_id,
                conversation_id=body.conversation_id,
            )
        return TokenResponse(
            token=token.token,
            room_name=token.room_name,
            livekit_url=token.livekit_url,
        )

    return app
