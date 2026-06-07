"""Image-generation backend configuration loaded from environment variables.

:class:`ImageBackendConfig` is the input to
:func:`persona.imagegen.load_image_backend` (spec 15 T05). Per
``docs/ENGINEERING_STANDARDS.md`` §2.1 + §5 and the project-wide
"env vars + Pydantic Settings only" rule, every runtime knob lives in an
env var; ``.env`` autoload is opt-in and the CLI's job, not this module's.

Mirrors :class:`persona.backends.config.BackendConfig` per the Spec 15
decisions gate paragraph #1; the env prefix is ``PERSONA_IMAGEGEN_`` so a
single process can host both a chat backend (``PERSONA_*``) and an image
backend (``PERSONA_IMAGEGEN_*``) without collision.

References:
    docs/specs/phase2/spec_15/decisions.md D-15-1 + D-15-2 +
    D-15-X-provider-moderation-default + D-15-X-pre-deduct-credits.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["DEFAULT_BASE_URLS", "ImageBackendConfig", "ImageProvider"]


ImageProvider = Literal["openai", "fal"]
"""Closed set of image-generation providers shipped at v0.1 (D-15-1).

OpenAI ``gpt-image-1.x`` is demo-primary (D-15-X-demo-primary-provider);
Flux 1.1 [pro] via fal.ai is the alternative for cost/style
differentiation. Extending this Literal goes through code review +
a new entry in :func:`persona.imagegen._factory.load_image_backend`.
"""


DEFAULT_BASE_URLS: dict[str, str] = {
    # The OpenAI SDK already appends ``/v1/images/generations`` so the base
    # URL needs the ``/v1/`` suffix. Matches the Spec 02 ``DEFAULT_BASE_URLS``
    # convention for OpenAI-compat providers.
    "openai": "https://api.openai.com/v1/",
    # fal.ai's queue endpoint is fronted by ``fal_client.subscribe_async``;
    # the SDK handles routing internally. The URL is recorded here for
    # observability + override scenarios (proxy, mock server).
    "fal": "https://queue.fal.run/",
}
"""Per-provider default base URLs. The caller can override via
:attr:`ImageBackendConfig.base_url` (proxies, self-hosted endpoints, or
mock servers in tests). Not exported from ``persona.imagegen.__init__`` —
internal to the factory + concrete backends.
"""


class ImageBackendConfig(BaseSettings):
    """Env-driven configuration for a single :class:`persona.imagegen.protocol.ImageBackend`.

    Reads from ``PERSONA_IMAGEGEN_*`` env vars by default.
    :meth:`from_env` constructs a config keyed on a different prefix
    when callers need to host multiple image backends in the same process
    (parallel of Spec 02's per-tier prefix pattern). The default
    construction (no overrides) is sufficient for v0.1 single-backend
    deployments.

    Attributes:
        provider: Which image backend to load (D-15-1).
        model: Model identifier within the provider. Defaults to OpenAI
            ``gpt-image-1``; for fal the caller sets this to
            ``"fal-ai/flux-pro/v1.1"`` (or a future variant) per
            D-15-X-demo-primary-provider note about gpt-image-1.x family
            successors.
        api_key: Provider API key. Stored as :class:`SecretStr` so
            ``repr(config)`` does not leak it. ``None`` means the env
            var was unset — the concrete backend constructor raises
            :class:`persona.imagegen.errors.ImageGenUnavailableError`
            at that point (fail-fast).
        base_url: Optional override for the provider's default endpoint
            (proxies, self-hosted endpoints, mock servers in tests). When
            ``None`` the concrete backend reads :data:`DEFAULT_BASE_URLS`.
        request_timeout_s: HTTP request timeout in seconds. Default 120s
            — image generation is materially slower than chat
            (research.md §1.1 community p50 ~10–25s for OpenAI medium
            quality; fal usually <10s but no published p99). 120s is the
            conservative upper bound that still surfaces dead-provider
            cases as ``ImageProviderError(reason="timeout")``.
        fal_safety_tolerance: fal.ai per-call ``safety_tolerance`` setting
            (D-15-X-provider-moderation-default). Range 1 (strict) – 6
            (permissive); defaults to ``2`` (the fal API default,
            conservative end). Per-persona or per-user overrides are a
            v0.2 candidate, not v0.1. Ignored when ``provider="openai"``.
    """

    model_config = SettingsConfigDict(
        env_prefix="PERSONA_IMAGEGEN_",
        extra="ignore",
    )

    provider: ImageProvider = "openai"
    model: str = "gpt-image-1"
    api_key: SecretStr | None = Field(default=None, repr=False)
    base_url: str | None = None
    request_timeout_s: float = Field(default=120.0, gt=0.0)
    fal_safety_tolerance: int = Field(default=2, ge=1, le=6)

    @classmethod
    def from_env(cls, prefix: str = "PERSONA_IMAGEGEN_") -> ImageBackendConfig:
        """Construct an :class:`ImageBackendConfig` reading from ``<prefix>*`` env vars.

        Callers needing to host multiple image backends in the same
        process pass a discriminating prefix (e.g.
        ``"PERSONA_IMAGEGEN_PRIMARY_"`` /
        ``"PERSONA_IMAGEGEN_ALTERNATE_"``). The default prefix matches
        the class-level :attr:`model_config` so a no-arg call is
        equivalent to ``ImageBackendConfig()``.

        Args:
            prefix: Env-var prefix to read from. Must end with an
                underscore for Pydantic Settings to compose names
                correctly.

        Returns:
            An :class:`ImageBackendConfig` populated from
            ``<prefix>PROVIDER``, ``<prefix>MODEL``, ``<prefix>API_KEY``,
            etc.
        """
        # Mirrors persona.backends.config.BackendConfig.from_env — a
        # subclass with the requested env_prefix; Pydantic Settings
        # composes ``env_prefix + field_name.upper()`` to read the env.

        class _Prefixed(cls):  # type: ignore[valid-type, misc]
            model_config = SettingsConfigDict(env_prefix=prefix, extra="ignore")

        return _Prefixed()
