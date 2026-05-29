"""Backend configuration loaded from environment variables.

``BackendConfig`` is the input to :func:`persona.backends.load_backend`.
Per the engineering standards (§2.1, §5) and decisions D-02-3 / D-02-4, every
runtime knob lives in an env var; ``.env`` autoload is opt-in and only
done by the CLI.

Per-tier overrides (spec 05) will construct ``BackendConfig.from_env`` with
a tier-specific prefix (e.g., ``PERSONA_TIER_FRONTIER_``). Spec 02 itself
uses the default ``PERSONA_`` prefix.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Provider", "BackendConfig", "DEFAULT_BASE_URLS"]

Provider = Literal[
    "anthropic",
    "openai",
    "deepseek",
    "groq",
    "together",
    "ollama",
    "local",
]


# Per-provider OpenAI-compatible base URLs. Lives here so T07 imports it; the
# user can override via ``BackendConfig.base_url`` for proxies or new
# providers. Not exported from ``persona.backends.__init__``.
DEFAULT_BASE_URLS: dict[str, str] = {
    # No trailing /v1/ for Anthropic: the `anthropic` SDK appends its own
    # /v1/messages, so a /v1/ suffix here produces /v1/v1/messages → 404
    # (D-10-9). The OpenAI-compat providers keep /v1/ — their SDK does not append it.
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1/",
    "deepseek": "https://api.deepseek.com/v1/",
    "groq": "https://api.groq.com/openai/v1/",
    "together": "https://api.together.xyz/v1/",
    "ollama": "http://localhost:11434",
}


class BackendConfig(BaseSettings):
    """Env-driven configuration for a single :class:`ChatBackend`.

    Reads from ``PERSONA_*`` env vars by default. ``from_env(prefix=...)``
    constructs a config keyed on a different prefix for per-tier overrides
    (used by spec 05's router).

    Attributes:
        provider: Which backend to load.
        model: Model identifier within the provider.
        api_key: Provider API key. Stored as :class:`SecretStr` so
            ``repr(config)`` does not leak it.
        base_url: Optional override for the provider's default endpoint
            (for proxies, self-hosted endpoints, or new providers not in
            :data:`DEFAULT_BASE_URLS`).
        max_tokens: Per-call generation budget.
        temperature: Sampling temperature.
        request_timeout_s: HTTP request timeout in seconds (D-02-3).
        local_model_id: HuggingFace model ID for ``provider="local"``.
        local_quantization: Quantisation mode for local models.
        local_device: Torch device for local models (``auto`` lets
            transformers pick).
    """

    model_config = SettingsConfigDict(
        env_prefix="PERSONA_",
        extra="ignore",
    )

    provider: Provider = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: SecretStr | None = Field(default=None, repr=False)
    base_url: str | None = None
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.0, ge=0.0)
    request_timeout_s: float = Field(default=60.0, gt=0.0)

    local_model_id: str | None = None
    local_quantization: Literal["4bit", "8bit", "none"] = "4bit"
    local_device: str = "auto"

    @classmethod
    def from_env(cls, prefix: str = "PERSONA_") -> BackendConfig:
        """Construct a :class:`BackendConfig` reading from ``<prefix>*`` env vars.

        Spec 05's router uses this with prefixes like
        ``PERSONA_TIER_FRONTIER_`` to keep per-tier configurations separate.

        Args:
            prefix: Env-var prefix to read from. Must end with an
                underscore for Pydantic Settings to compose names correctly.

        Returns:
            A ``BackendConfig`` populated from ``<prefix>PROVIDER``,
            ``<prefix>MODEL``, ``<prefix>API_KEY``, etc.
        """
        # Build a subclass with the requested prefix; Pydantic Settings
        # composes ``env_prefix + field_name.upper()`` to read the env.

        class _Tiered(cls):  # type: ignore[valid-type, misc]
            model_config = SettingsConfigDict(env_prefix=prefix, extra="ignore")

        return _Tiered()
