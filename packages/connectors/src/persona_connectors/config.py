"""Settings for the persona-connectors service (Spec C1 T1).

Every knob lands here via environment variables — twelve-factor discipline (the
Spec 08 ``APIConfig`` / V1 ``VoiceConfig`` precedent). Connector-specific knobs
are prefixed ``PERSONA_CONNECTORS_``; the open-core edition reads the shared,
prefix-less ``PERSONA_EDITION`` var (Spec 33), exactly as api/web/voice do.

This module is part of the import-decoupled surface — it does NOT import
``persona_api`` (the api-coupling lives only in
:mod:`persona_connectors.composition`).
"""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["ConnectorConfig"]


class ConnectorConfig(BaseSettings):
    """Environment-driven settings for the persona-connectors service."""

    model_config = SettingsConfigDict(
        env_prefix="PERSONA_CONNECTORS_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        # Honor an explicit ``ConnectorConfig(edition=...)`` kwarg (the field
        # name) alongside the ``PERSONA_EDITION`` validation_alias (Spec 33).
        populate_by_name=True,
    )

    # --- Open-core edition (Spec 33) ---
    # Reads the SAME ``PERSONA_EDITION`` var as api/web/voice (no prefix).
    # ``community`` (default): single local owner, no auth, no credit metering.
    # ``cloud``: Clerk JWT + persona ownership + credits + multi-tenant RLS.
    edition: str = Field(default="community", validation_alias="PERSONA_EDITION")
    community_owner_id: str = Field(default="local-owner")
    community_owner_email: str = Field(default="local@localhost")

    @property
    def is_cloud(self) -> bool:
        """Whether this process runs the commercial cloud edition."""
        return self.edition.strip().lower() == "cloud"

    # --- Conversation boundaries (C1-D-3) ---
    # The per-(owner, platform, channel, persona) idle gap that ends a persona's
    # conversation. Tens of minutes, tunable; low-stakes (memory persists). Lazy
    # expiry on the next inbound — no background sweeper.
    idle_timeout_minutes: int = Field(default=30, gt=0)

    # --- Database (RLS-scoped persona-core direct access; cloud) ---
    # Same persona_app non-superuser role as persona-api (D-07-5); RLS scopes
    # every connection via the ``current_user_id`` contextvar the composition
    # root sets per inbound message (D-C1-X-rls-spine).
    database_url: str = Field(default="")
    db_pool_size: int = Field(default=5, gt=0)

    # --- Community-edition local persistence (Spec 33) ---
    community_db_path: str = Field(default="./persona_community.db")
    community_memory_path: str = Field(default="./persona_community_memory")

    # --- JWT verification (matches the JwtVerifierConfig Protocol shape) ---
    # Identical surface to ``APIConfig`` / ``VoiceConfig`` so the same
    # ``persona.auth.jwt_verifier.make_jwt_verifier`` consumes this via
    # structural typing — for resolving the authenticated identity at linking.
    jwt_secret: SecretStr | None = Field(default=None)
    jwt_public_key: SecretStr | None = Field(default=None)
    jwt_algorithms: str = Field(default="HS256")
    jwt_audience: str | None = Field(default=None)

    @field_validator("jwt_algorithms", mode="before")
    @classmethod
    def _normalise_algorithms(cls, v: object) -> str:
        """Allow the ``PERSONA_CONNECTORS_JWT_ALGORITHMS=HS256,RS256`` env form."""
        if v is None:
            return "HS256"
        return str(v)

    @property
    def jwt_algorithms_list(self) -> list[str]:
        """The configured algorithms as a list (consumed by ``make_jwt_verifier``)."""
        return [a.strip() for a in self.jwt_algorithms.split(",") if a.strip()]
