"""Sandbox-pool configuration via env vars (spec 12 T09b; locked by D-12-17).

Read once at process start through ``pydantic-settings``; the composition root
constructs a :class:`SandboxPoolConfig` and passes the values into
:class:`persona_api.sandbox.pool.SandboxPool`. Aligns with the project rule
"Config via env vars + Pydantic Settings only" (CLAUDE.md).

The ``PERSONA_SANDBOX_`` prefix matches the existing ``PERSONA_SANDBOX_IMAGE``
env var documented in ``.env.example`` — sandbox-scope config, distinct from
the API-scope ``PERSONA_API_*`` knobs in :class:`persona_api.config.APIConfig`.

**D-12-17 locked defaults:**

  - ``warm_pool_size`` = 0 — no idle slots; first acquire pays the substrate
    cold-start (~2.305s p95 per Gate 1) within the acquire call. Nonzero
    values require the warm-pool maintainer (deferred to a future spec when
    telemetry triggers the flip; see D-12-17 flip-triggers).
  - ``reap_interval_s`` = 60 — background reaper sweep cadence.
  - ``idle_timeout_s`` = 300 — session staleness before reap.
  - ``max_per_user`` = 2 — per-tenant concurrent-session cap; bounds the
    multi-tenant attack surface (SCP-12-1 reference).
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["SandboxPoolConfig"]


class SandboxPoolConfig(BaseSettings):
    """Environment-driven configuration for :class:`SandboxPool` (D-12-17)."""

    model_config = SettingsConfigDict(
        env_prefix="PERSONA_SANDBOX_", extra="ignore", populate_by_name=True
    )

    warm_pool_size: int = Field(
        default=0,
        description=(
            "Idle slots maintained per (user_id, conversation_id). v0.1 locks to 0 "
            "(D-12-17). Nonzero requires a maintainer task that v0.1 does not ship; "
            "production sets this only after the D-12-17 warm-pool flip-trigger fires."
        ),
    )
    reap_interval_s: float = Field(
        default=60.0,
        description="Background reaper sweep cadence. D-12-17 default: 60s.",
    )
    idle_timeout_s: float = Field(
        default=300.0,
        description=(
            "Seconds without activity before a session is reaped. D-12-17 default: "
            "300s (matches HostedSandbox.timeout_default_s by intent)."
        ),
    )
    max_per_user: int = Field(
        default=2,
        description=(
            "Per-user concurrent-session cap. D-12-17 default: 2. Bounds "
            "multi-tenant attack surface (SCP-12-1); per-VM properties don't "
            "compound across slots."
        ),
    )

    @field_validator("warm_pool_size")
    @classmethod
    def _v01_locks_warm_pool_to_zero(cls, v: int) -> int:
        """D-12-17 v0.1 lock: nonzero warm-pool size requires the maintainer task.

        The maintainer code does not ship in v0.1 (T09b deliberately scopes to
        the reaper only). Production flips this to a positive value only after
        the D-12-17 telemetry-driven flip-trigger fires AND the maintainer
        spec lands. Surfacing the gap explicitly here keeps the env var
        forward-compatible without silently accepting a value the pool
        can't honour.
        """
        if v < 0:
            msg = f"warm_pool_size must be >= 0; got {v}"
            raise ValueError(msg)
        if v > 0:
            msg = (
                f"warm_pool_size={v} requires the warm-pool maintainer task, which "
                "v0.1 does not ship (D-12-17 locks the v0.1 value to 0; nonzero "
                "values land in a follow-up spec when production telemetry "
                "triggers the warm-pool flip)."
            )
            raise ValueError(msg)
        return v

    @field_validator("reap_interval_s", "idle_timeout_s")
    @classmethod
    def _positive_seconds(cls, v: float) -> float:
        if v <= 0:
            msg = f"value must be > 0; got {v}"
            raise ValueError(msg)
        return v

    @field_validator("max_per_user")
    @classmethod
    def _positive_cap(cls, v: int) -> int:
        if v < 1:
            msg = f"max_per_user must be >= 1; got {v}"
            raise ValueError(msg)
        return v
