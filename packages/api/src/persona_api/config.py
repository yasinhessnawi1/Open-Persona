"""Configuration for persona-api, loaded from environment variables (spec 08, T01).

Twelve-Factor (ENGINEERING_STANDARDS.md §4): every runtime knob is an env var; no
YAML config files, no Hydra. Values are read once at process start via Pydantic
Settings and injected downstream — code accepts an :class:`APIConfig` instance
rather than reading ``os.environ`` directly.

The ``PERSONA_API_`` prefix keeps the API's own knobs distinct from
``persona-core``'s ``PERSONA_`` config (the API constructs a ``PersonaCoreConfig``
separately for the toolbox). ``DATABASE_URL`` / ``APP_DATABASE_URL`` are read
without the prefix to match the spec-07 conventions the migration + Docker harness
already use.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["APIConfig"]


class APIConfig(BaseSettings):
    """Environment-driven configuration for the hosted API.

    Attributes:
        database_url: The superuser/owner DSN used to run migrations and
            (in dev/tests) the store engine. Sync psycopg3 dialect. Read from
            ``DATABASE_URL`` (no prefix — matches spec 07).
        app_database_url: The non-superuser ``persona_app`` DSN the request path
            connects with so RLS is enforced (superusers bypass it). Falls back
            to ``database_url`` when unset (single-role dev). Read from
            ``APP_DATABASE_URL``.
        jwt_secret: Symmetric signing key for HS256 token verification (the
            v0.1/test path; D-08-4). Never logged. Empty disables HS256 verify.
        jwt_public_key: PEM public key for RS256 verification (the real
            Clerk/Supabase JWKS path; D-08-4). Never logged.
        jwt_algorithms: Allowed JWT algorithms (comma-separated).
        jwt_audience: Expected ``aud`` claim; empty skips the audience check.
        embedder_model: Sentence-transformers model for persona memory
            embedding (architecture §9.6; D-08-8). bge-small-en-v1.5 → 384-dim.
        db_pool_size: Connection-pool size for the request engine. >1 so a slow
            sync store call doesn't serialise concurrent CRUD (research §5).
        rate_limit_default: Default per-user-per-endpoint-per-minute limit (§6).
        rate_limit_messages / rate_limit_runs / rate_limit_author: Per-endpoint
            overrides (§6 table).
        authoring_credit_cost: Flat credit deduction per authoring call (D-08-6,
            §11 risk).
    """

    model_config = SettingsConfigDict(
        env_prefix="PERSONA_API_", extra="ignore", populate_by_name=True
    )

    # DB DSNs — read WITHOUT the prefix (spec-07 convention: DATABASE_URL /
    # APP_DATABASE_URL). validation_alias overrides the env_prefix per field.
    database_url: str = Field(default="", validation_alias="DATABASE_URL", repr=False)
    app_database_url: str = Field(default="", validation_alias="APP_DATABASE_URL", repr=False)

    # Auth (D-08-4). Secrets never logged.
    jwt_secret: SecretStr | None = Field(default=None, repr=False)
    jwt_public_key: SecretStr | None = Field(default=None, repr=False)
    jwt_algorithms: str = "HS256"
    jwt_audience: str = ""

    # Memory embedding (D-08-8).
    embedder_model: str = "BAAI/bge-small-en-v1.5"

    # Audit-log root for the store-mutation JSONL audit (spec 01 AuditLogger).
    # Distinct from the api `audit_log` TABLE (T12) — see spec-07 handoff.
    audit_root: str = "./.persona_audit"

    # Connection pool (research §5 — roomy pool removes store/CRUD contention).
    db_pool_size: int = 5

    # Rate limiting (§6). backend: "memory" (dev/tests) or "postgres".
    rate_limit_backend: str = "memory"
    rate_limit_default: int = 60
    rate_limit_messages: int = 20
    rate_limit_runs: int = 5
    rate_limit_author: int = 3

    # LLM-assisted authoring (§6.3): the model tier the authoring endpoint uses.
    authoring_tier: str = "frontier"

    # Credits (D-08-6): flat per successful chat turn + per authoring call.
    credits_per_turn: int = 1
    authoring_credit_cost: int = 1000

    # CORS origins allowed to call the API from a browser (spec-09 web app).
    # Comma-separated; the web dev server is http://localhost:3000 by default.
    # Empty disables CORS (server-to-server only). Read from PERSONA_API_CORS_ORIGINS.
    cors_origins: str = "http://localhost:3000"

    # Spec 13 D-13-4: per-deployment workspace root for uploaded images (and
    # later per-persona tool artefacts). Each upload lands at
    # ``<workspace_root>/<owner_id>/<persona_id>/uploads/<digest><ext>`` and is
    # resolved via ``persona.tools._sandbox.resolve_sandbox_path``. Read from
    # ``PERSONA_API_WORKSPACE_ROOT``; defaults to a ``.persona_work`` dir under
    # the process cwd so a clean checkout runs without env setup.
    workspace_root: Path = Field(default_factory=lambda: Path.cwd() / ".persona_work")

    @property
    def effective_app_database_url(self) -> str:
        """The DSN the request path connects with (RLS-enforced).

        Prefers ``app_database_url`` (the non-superuser ``persona_app`` role);
        falls back to ``database_url`` for single-role dev. Coerces a stray
        async DSN to the sync psycopg3 dialect (D-07-1).
        """
        url = self.app_database_url or self.database_url
        return url.replace("+asyncpg", "+psycopg")

    @property
    def jwt_algorithms_list(self) -> list[str]:
        """The allowed JWT algorithms as a list."""
        return [a.strip() for a in self.jwt_algorithms.split(",") if a.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        """The CORS-allowed origins as a list (empty disables CORS)."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
