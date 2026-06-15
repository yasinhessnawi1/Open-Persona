"""Settings for the persona-voice service (spec V1 T04).

All knobs land here via environment variables — twelve-factor discipline (per
the spec 08 ``APIConfig`` precedent). Secrets are :class:`pydantic.SecretStr`
so they never leak into ``repr()`` or logs by accident.

The JWT verifier fields (``jwt_secret`` / ``jwt_public_key`` / ``jwt_algorithms``
/ ``jwt_audience``) match the same names ``APIConfig`` exposes, so the same
``persona.auth.jwt_verifier.make_jwt_verifier`` (D-V1-X-jwt-verifier-extraction)
consumes either settings class via the structural ``JwtVerifierConfig`` Protocol.
This is the seam that lets persona-api and persona-voice share the auth surface
without persona-voice taking a persona-api dependency.
"""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["VoiceConfig"]


class VoiceConfig(BaseSettings):
    """Environment-driven settings for the persona-voice service."""

    model_config = SettingsConfigDict(
        env_prefix="PERSONA_VOICE_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # --- LiveKit substrate (D-V1-1 branch (A), D-V1-X-livekit-server-deployment) ---
    # `LIVEKIT_URL` is the WebSocket URL the client uses to connect to the
    # LiveKit Server (ws:// for self-hosted; wss:// for production). The
    # server-side AccessToken JWTs are signed with `LIVEKIT_API_SECRET` and
    # consumed by the same self-hosted LiveKit Server, so the secret never
    # leaves the deployment.
    livekit_url: str = Field(default="ws://localhost:7880")
    livekit_api_key: SecretStr = Field(default=SecretStr(""))
    livekit_api_secret: SecretStr = Field(default=SecretStr(""))
    # Default access-token TTL (10 min) — long enough for the client to
    # complete signaling + the call's first few minutes, short enough that a
    # leaked token expires quickly. The LiveKit Server re-checks expiry on
    # every connection event.
    livekit_token_ttl_s: int = Field(default=600)

    # --- JWT verification (matches the JwtVerifierConfig Protocol shape) ---
    # Identical surface to `APIConfig`'s JWT fields so the same
    # `make_jwt_verifier` from persona-core consumes either via structural
    # typing (D-V1-X-jwt-verifier-extraction).
    jwt_secret: SecretStr | None = Field(default=None)
    jwt_public_key: SecretStr | None = Field(default=None)
    jwt_algorithms: str = Field(default="HS256")
    jwt_audience: str | None = Field(default=None)

    # --- Database (RLS-scoped persona-core direct access per D-V1-4) ---
    # Same persona_app non-superuser role as persona-api (D-07-5); RLS scopes
    # every connection via the request-scoped contextvar.
    database_url: str = Field(default="")

    # --- Dev agent worker (spec V6 A0, D-V6-X-agent-worker) ---
    # When true, ``POST /v1/voice/token`` ALSO launches an in-process agent
    # session that joins the call's Room and becomes the persona on the call
    # (the dev/operator-pass-grade composition-root runner). Default FALSE —
    # production worker-ops are a separate forward-item, and existing token-only
    # deployments + tests are unaffected. Requires ``database_url`` +
    # ``livekit_api_*`` + the provider keys (PERSONA_STT_*/PERSONA_TTS_*/tiers).
    agent_inprocess: bool = Field(default=False)

    @field_validator("jwt_algorithms", mode="before")
    @classmethod
    def _normalise_algorithms(cls, v: object) -> str:
        """Allow ``PERSONA_VOICE_JWT_ALGORITHMS=HS256,RS256`` env-var form."""
        if v is None:
            return "HS256"
        return str(v)

    @property
    def jwt_algorithms_list(self) -> list[str]:
        """Computed list form consumed by ``make_jwt_verifier``."""
        return [a.strip() for a in self.jwt_algorithms.split(",") if a.strip()]
