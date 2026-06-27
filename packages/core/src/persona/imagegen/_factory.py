"""``load_image_backend()`` factory — dispatches an
:class:`ImageBackendConfig` to the right concrete :class:`ImageBackend`.

Mirrors :mod:`persona.backends._factory` per the Spec 15 decisions gate
paragraph #1 ("Mirror Spec 02 verbatim — ``ImageBackend`` is
``ChatBackend``'s shape twin"). The concrete backends
(:class:`persona.imagegen.openai_image.OpenAIImageBackend`,
:class:`persona.imagegen.fal_image.FalImageBackend`) are imported lazily
inside :func:`load_image_backend` so the :mod:`persona.imagegen` package
stays importable before T06 / T07 land — and so callers that only need
the Protocol / config / boundary types do not pay the import cost of the
provider SDKs (``openai`` is already a Spec 02 dep but ``fal-client`` is
new in T07).

Spec 20 T17 adds :func:`load_image_backend_from_env`, the env-driven
entry point that mirrors :func:`persona_runtime.tier.tier_registry_from_env`:
when ``PERSONA_IMAGEGEN_MODELS`` is set, the function parses the CSV via
T11's :func:`persona.backends.credentials.parse_models_list`, resolves
each provider's credentials, and wraps N≥2 resolved backends in a
:class:`~persona.imagegen.multi_model_image.MultiModelImageBackend`. The
backward-compat single-backend triplet path falls through to
:func:`load_image_backend` with an :class:`ImageBackendConfig`
constructed from the existing ``PERSONA_IMAGEGEN_*`` triplet.

References:
    docs/specs/phase2/spec_15/decisions.md gate paragraph #1 + D-15-1.
    docs/specs/phase2/spec_20/decisions.md D-20-15 + D-20-17 + D-20-18.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import SecretStr

from persona.backends.credentials import (
    ProviderCredentialResolver,
    filter_openrouter_free_mode,
)
from persona.backends.errors import (
    LocalProviderInModelsListError,
    MalformedTierModelsError,
    ProviderCredentialMissingError,
    TierNotConfiguredError,
)
from persona.imagegen.config import DEFAULT_BASE_URLS, ImageBackendConfig
from persona.imagegen.errors import ImageProviderError
from persona.logging import get_logger

if TYPE_CHECKING:
    from persona.backends.openrouter_catalog import OpenRouterSubscriptionMode
    from persona.imagegen.protocol import ImageBackend

__all__ = ["load_image_backend", "load_image_backend_from_env"]

_LOG = get_logger("imagegen.factory")

_IMAGEGEN_TIER_NAME: str = "imagegen"
_MODELS_ENV_VAR: str = "PERSONA_IMAGEGEN_MODELS"
_TRIPLET_VARS: tuple[str, str, str] = (
    "PERSONA_IMAGEGEN_PROVIDER",
    "PERSONA_IMAGEGEN_MODEL",
    "PERSONA_IMAGEGEN_API_KEY",
)
_IMAGE_PROVIDERS: frozenset[str] = frozenset(
    {"openai", "fal", "nvidia", "openrouter", "cloudflare"}
)
"""Closed set of providers with concrete :class:`ImageBackend` implementations.

T11's :func:`persona.backends.credentials.parse_models_list` validates
against the chat-side Provider Literal (anthropic / openai / deepseek /
groq / together / nvidia), which excludes ``fal`` (image-only). The
image-gen factory needs its own parser that validates against the
image-side provider set instead. The parser mirrors T11's D-20-17 case (d)
failure modes verbatim — same ``reason`` codes, same context shape — so
operator-facing surfaces stay symmetric across chat and image-gen.
"""

_LOCAL_PROVIDERS_REJECTED: frozenset[str] = frozenset({"local", "ollama"})
"""D-20-18 — same EXPLICIT REJECT set as the chat-side parser.

``local`` / ``ollama`` have no image-backend implementations either, but
we keep the explicit reject here so the error class
(:class:`LocalProviderInModelsListError`) and ``hint`` carry forward
consistently across surfaces.
"""

_LOCAL_REJECT_HINT: str = "use PERSONA_IMAGEGEN_PROVIDER single-backend fast path"


_SUPPORTED_PROVIDERS: tuple[str, ...] = ("openai", "fal", "nvidia", "openrouter", "cloudflare")
"""Closed set of provider identifiers shipped at v0.1 (D-15-1).

Kept in sync with :data:`persona.imagegen.config.ImageProvider`; the
factory dispatches against this tuple so an unknown provider error
carries the supported list in its message and ``context``.
"""


def load_image_backend(config: ImageBackendConfig) -> ImageBackend:
    """Construct the concrete :class:`ImageBackend` for ``config.provider``.

    Mirrors :func:`persona.backends._factory.load_backend`. Concrete
    backend imports are lazy so :mod:`persona.imagegen` stays importable
    even when the alternate provider SDK is not installed; the concrete
    backend's ``__init__`` still raises a clear
    :class:`persona.imagegen.errors.ImageGenUnavailableError` if its
    SDK or API key is missing.

    Args:
        config: Image backend configuration. ``config.provider`` must be
            one of the values in :data:`_SUPPORTED_PROVIDERS`; otherwise
            :class:`ImageProviderError` is raised.

    Returns:
        A concrete backend implementing the
        :class:`persona.imagegen.protocol.ImageBackend` Protocol.

    Raises:
        ImageGenUnavailableError: ``api_key`` missing or rejected at
            construction time (fail-fast per the Spec 02 D-02-*
            construction-time-fail-fast precedent).
        ImageProviderError: ``config.provider`` is not in the supported
            set; ``context["provider"]`` carries the offending value and
            ``context["supported"]`` the comma-joined supported list.
    """
    provider = config.provider
    if provider == "openai":
        # Lazy import keeps ``persona.imagegen`` importable before T06
        # lands the OpenAI image backend module; the concrete backend's
        # ``__init__`` raises a clear
        # :class:`persona.imagegen.errors.ImageGenUnavailableError` if
        # the ``api_key`` is missing.
        from persona.imagegen.openai_image import (
            OpenAIImageBackend,
        )

        return OpenAIImageBackend(config)
    if provider == "fal":
        # Lazy import keeps ``persona.imagegen`` importable before T07
        # lands the fal backend module (``fal-client`` is a new dep
        # declared in ``packages/core/pyproject.toml`` by T07); the
        # concrete backend's ``__init__`` raises a clear
        # :class:`persona.imagegen.errors.ImageGenUnavailableError` if
        # the ``api_key`` is missing.
        from persona.imagegen.fal_image import (
            FalImageBackend,
        )

        return FalImageBackend(config)
    if provider == "nvidia":
        # Spec 20 T10 — NVIDIA Build Catalog. The concrete backend's
        # ``__init__`` fail-fasts on missing api_key, on the
        # D-20-X-flux-1-dev-license-block model set, and on unknown
        # models (``reason="unsupported_model"``).
        from persona.imagegen.nvidia_image import (
            NvidiaImageBackend,
        )

        return NvidiaImageBackend(config)
    if provider == "openrouter":
        # Spec 22 T09a — OpenRouter image-gen rides the chat-completions
        # surface (no /images/generations route). The concrete backend's
        # ``__init__`` fail-fasts on missing api_key. Paid-mode only — the
        # free-mode drop of openrouter image entries lives at the runtime
        # composition layer (T14), where the subscription state resolved by
        # T13 is available (core must not import persona-runtime — D-22-20).
        from persona.imagegen.openrouter_image import (
            OpenRouterImageBackend,
        )

        return OpenRouterImageBackend(config)
    if provider == "cloudflare":
        # Spec 25 T15/T16 (D-25-11..14) — Cloudflare Workers AI text-to-image
        # (the truly-free path; flux-1-schnell GA primary). The concrete
        # backend's ``__init__`` fail-fasts on missing api_key, missing
        # ``cloudflare_account_id`` (D-25-12), and unknown models
        # (``reason="unsupported_model"``).
        from persona.imagegen.cloudflare_image import (
            CloudflareImageBackend,
        )

        return CloudflareImageBackend(config)
    supported = ", ".join(_SUPPORTED_PROVIDERS)
    raise ImageProviderError(
        f"unknown image provider {provider!r}; expected one of {supported}",
        context={"provider": str(provider), "supported": supported},
    )


def _parse_image_models_list(raw_value: str) -> list[tuple[str, str]]:
    """Parse ``PERSONA_IMAGEGEN_MODELS`` into ``(provider, model)`` slots.

    Mirrors :func:`persona.backends.credentials.parse_models_list` verbatim
    (same ``reason`` codes, same exception classes, same context shape) but
    validates the provider token against :data:`_IMAGE_PROVIDERS` instead
    of the chat-side Provider Literal. T11's parser cannot accept ``fal``
    (image-only) so the image-gen factory carries its own narrow validator.

    Args:
        raw_value: Raw env-var value as read by the caller.

    Returns:
        Parsed list of ``(provider, model)`` tuples preserving CSV order
        (fallback chain order is meaningful — see D-20-4).

    Raises:
        MalformedTierModelsError: D-20-17 case (d) — empty input, empty
            CSV entry, missing slash, unknown provider (outside
            :data:`_IMAGE_PROVIDERS`), or empty model component.
        LocalProviderInModelsListError: D-20-18 — ``local`` or ``ollama``
            token in any slot.
    """
    stripped = raw_value.strip()
    if not stripped:
        raise MalformedTierModelsError(
            f"PERSONA_{_IMAGEGEN_TIER_NAME.upper()}_MODELS is empty after strip",
            context={
                "tier": _IMAGEGEN_TIER_NAME,
                "value": raw_value,
                "reason": "empty_after_strip",
            },
        )

    results: list[tuple[str, str]] = []
    for position, raw_entry in enumerate(stripped.split(",")):
        entry = raw_entry.strip()
        if not entry:
            raise MalformedTierModelsError(
                f"PERSONA_{_IMAGEGEN_TIER_NAME.upper()}_MODELS has empty CSV "
                f"entry at position {position}",
                context={
                    "tier": _IMAGEGEN_TIER_NAME,
                    "value": raw_value,
                    "reason": "empty_csv_entry",
                    "position": str(position),
                },
            )
        if "/" not in entry:
            raise MalformedTierModelsError(
                f"PERSONA_{_IMAGEGEN_TIER_NAME.upper()}_MODELS entry "
                f"{entry!r} missing slash separator at position {position}",
                context={
                    "tier": _IMAGEGEN_TIER_NAME,
                    "value": raw_value,
                    "reason": "missing_slash",
                    "position": str(position),
                },
            )
        provider_token, _, model_token = entry.partition("/")
        provider_token = provider_token.strip()
        model_token = model_token.strip()
        is_image_provider = provider_token in _IMAGE_PROVIDERS
        is_local_reject = provider_token in _LOCAL_PROVIDERS_REJECTED
        if not is_image_provider and not is_local_reject:
            supported = ", ".join(sorted(_IMAGE_PROVIDERS))
            raise MalformedTierModelsError(
                f"PERSONA_{_IMAGEGEN_TIER_NAME.upper()}_MODELS unknown provider "
                f"{provider_token!r} at position {position}; "
                f"expected one of {supported}",
                context={
                    "tier": _IMAGEGEN_TIER_NAME,
                    "value": raw_value,
                    "reason": "unknown_provider",
                    "position": str(position),
                    "supported": supported,
                },
            )
        if not model_token:
            raise MalformedTierModelsError(
                f"PERSONA_{_IMAGEGEN_TIER_NAME.upper()}_MODELS empty model "
                f"after slash at position {position}",
                context={
                    "tier": _IMAGEGEN_TIER_NAME,
                    "value": raw_value,
                    "reason": "empty_model",
                    "position": str(position),
                },
            )
        if provider_token in _LOCAL_PROVIDERS_REJECTED:
            # D-20-18 EXPLICIT REJECT — same shape as T11's chat-side parser.
            raise LocalProviderInModelsListError(
                f"PERSONA_{_IMAGEGEN_TIER_NAME.upper()}_MODELS rejects "
                f"{provider_token!r} at position {position}",
                context={
                    "tier": _IMAGEGEN_TIER_NAME,
                    "position": str(position),
                    "hint": _LOCAL_REJECT_HINT,
                },
            )
        results.append((provider_token, model_token))
    return results


def load_image_backend_from_env(
    *, openrouter_subscription_mode: OpenRouterSubscriptionMode | None = None
) -> ImageBackend:
    """Build a single :class:`ImageBackend` from the environment (D-20-17).

    Four-case precedence (Spec 20 D-20-17) mirroring the chat-side
    :func:`persona_runtime.tier.tier_registry_from_env`:

    * **(a) MODELS-only set** — ``PERSONA_IMAGEGEN_MODELS`` parses to N
      slots; build a :class:`~persona.imagegen.multi_model_image.MultiModelImageBackend`
      wrapper around the N resolved backends (or return the bare backend
      when N==1 after credential resolution). No log.
    * **(b) Triplet-only set** — ``PERSONA_IMAGEGEN_PROVIDER`` +
      ``..._MODEL`` + ``..._API_KEY`` all set; build a single backend via
      :func:`load_image_backend` (backward-compat fast path). No log.
    * **(c) Both set** — MODELS wins; INFO log identifies the ignored
      triplet vars (operator-mid-migration aid).
    * **(d) Malformed MODELS** — :class:`MalformedTierModelsError` propagates
      from the parser.

    D-20-15 per-slot disposition (ALL fail → :class:`TierNotConfiguredError`;
    ≥1 resolves → wrapper with remaining, WARN per skipped).

    D-20-18: ``local`` / ``ollama`` tokens rejected by
    :func:`parse_models_list` (irrelevant for image-gen but the parser is
    shared with the chat side).

    Spec 22 (D-22-20): when ``openrouter_subscription_mode`` is ``"free"``,
    ALL ``openrouter/<model>`` entries are dropped at config-validation time
    with a WARN per entry — zero ``:free`` image-output models exist on
    OpenRouter, so an OpenRouter image entry is unusable in free-mode
    (fail-fast over a call-time 402). ``None`` / ``"paid"`` is a no-op.

    Args:
        openrouter_subscription_mode: Resolved OpenRouter mode (``"free"`` /
            ``"paid"``) or ``None`` (no OpenRouter free-mode filtering). The
            value is resolved by the runtime subscription resolver
            (Spec 22 T13) and injected so ``persona-core`` stays free of any
            ``persona-runtime`` dependency.

    Returns:
        Either a bare :class:`ImageBackend` (case (b), or case (a) with
        N==1 after credential resolution) or a wrapping
        :class:`MultiModelImageBackend` (case (a)/(c) with N≥2).

    Raises:
        TierNotConfiguredError: D-20-15 ALL-fail branch — every provider in
            ``PERSONA_IMAGEGEN_MODELS`` lacked a credential.
        ImageProviderError: A MODELS-list slot referenced a provider with no
            :class:`ImageBackend` implementation (chat-only provider like
            ``anthropic``, ``deepseek``, ``groq``, ``together``).
        MalformedTierModelsError: D-20-17 case (d).
        LocalProviderInModelsListError: D-20-18 rejection.
    """
    env_snapshot: dict[str, str] = dict(os.environ)
    raw_models = env_snapshot.get(_MODELS_ENV_VAR, "")
    if not raw_models.strip():
        # Cases (b): no MODELS — fall back to the legacy triplet path.
        # `ImageBackendConfig.from_env` reads the triplet; the concrete
        # backend's `__init__` fail-fasts on missing api_key.
        return load_image_backend(ImageBackendConfig.from_env())

    # Cases (a) / (c): MODELS wins. D-20-17 case (d) is raised by the parser.
    # Mirror T11's :func:`parse_models_list` discipline but validate against
    # :data:`_IMAGE_PROVIDERS` (image-gen surface narrows the chat-side
    # Provider Literal — ``fal`` is image-only and not in the chat Literal,
    # so T11's parser can't accept it).
    parsed = _parse_image_models_list(raw_models)

    # D-22-20: in free-mode, drop ALL openrouter image entries (no :free image
    # models exist). keep_free_suffix=False → every openrouter entry drops with
    # a WARN. No-op when mode is None / paid.
    parsed = filter_openrouter_free_mode(
        parsed,
        mode=openrouter_subscription_mode,
        tier_name=_IMAGEGEN_TIER_NAME,
        keep_free_suffix=False,
    )

    # D-20-17 case (c): both forms set — emit INFO log naming ignored triplet vars.
    ignored = sorted(var for var in _TRIPLET_VARS if env_snapshot.get(var, "").strip())
    if ignored:
        _LOG.info(
            "PERSONA_IMAGEGEN_MODELS takes precedence; ignoring triplet vars "
            "{ignored} (D-20-17 case (c))",
            ignored=",".join(ignored),
        )

    # Cloudflare's account id has an image-gen-specific env var
    # (``PERSONA_IMAGEGEN_CLOUDFLARE_ACCOUNT_ID``) that predates the chat
    # provider's generic ``PERSONA_CLOUDFLARE_ACCOUNT_ID``. The shared
    # ``ProviderCredentialResolver`` (chat path) reads only the generic name and
    # fail-fasts on it when assembling cloudflare's chat base URL. The image-gen
    # MODELS path must keep honouring its own var: resolve the account id here
    # via the fallback chain (image-specific preferred, generic accepted) and
    # seed the generic name in the resolver's env snapshot so
    # ``resolve('cloudflare')`` succeeds under either namespace. The cloudflare
    # ImageBackend builds its own request URL from the bare ``DEFAULT_BASE_URLS``
    # prefix + account id, so the chat base URL the resolver assembles is
    # discarded for cloudflare slots below.
    cloudflare_account_id = (
        env_snapshot.get("PERSONA_IMAGEGEN_CLOUDFLARE_ACCOUNT_ID")
        or env_snapshot.get("PERSONA_CLOUDFLARE_ACCOUNT_ID")
        or None
    )
    resolver_env = dict(env_snapshot)
    if cloudflare_account_id and not resolver_env.get("PERSONA_CLOUDFLARE_ACCOUNT_ID"):
        resolver_env["PERSONA_CLOUDFLARE_ACCOUNT_ID"] = cloudflare_account_id

    resolver = ProviderCredentialResolver(env=resolver_env)
    resolved_backends: list[ImageBackend] = []
    skipped: list[tuple[str, str]] = []
    for position, (provider, model) in enumerate(parsed):
        try:
            creds = resolver.resolve(provider)  # type: ignore[arg-type]
        except ProviderCredentialMissingError as exc:
            env_var = exc.context.get("env_var", "")
            _LOG.warning(
                "provider credential missing; skipping image backend slot "
                "tier={tier} provider={provider} env_var={env_var} "
                "position={position}/{total}",
                tier=_IMAGEGEN_TIER_NAME,
                provider=provider,
                env_var=env_var,
                position=position,
                total=len(parsed),
            )
            skipped.append((provider, env_var))
            continue
        # Synthesize a per-slot ImageBackendConfig. The provider's default
        # base URL is sourced from :data:`DEFAULT_BASE_URLS` if the resolver
        # didn't carry one (e.g., the per-provider env var was unset).
        if provider == "cloudflare":
            # The shared resolver assembles cloudflare's CHAT base URL
            # (``.../accounts/{id}/ai/v1/``). The cloudflare ImageBackend builds
            # its own request URL from the bare ``.../accounts/`` prefix +
            # account id, so the chat URL must not leak in here — use the
            # image-gen DEFAULT_BASE_URLS prefix and let the backend append.
            slot_base_url = DEFAULT_BASE_URLS.get(provider)
        else:
            slot_base_url = creds.base_url or DEFAULT_BASE_URLS.get(provider)
        slot_config = ImageBackendConfig(
            provider=provider,  # type: ignore[arg-type]
            model=model,
            api_key=creds.api_key if creds.api_key is not None else SecretStr(""),
            base_url=slot_base_url,
            # Spec 25 D-25-14: thread the single process-level Cloudflare
            # account id into every slot (ignored by non-cloudflare backends);
            # a cloudflare slot in a cross-provider MODELS list needs it.
            # The image-gen-specific ``PERSONA_IMAGEGEN_CLOUDFLARE_ACCOUNT_ID``
            # is preferred (it predates the chat provider's generic
            # ``PERSONA_CLOUDFLARE_ACCOUNT_ID``), with the generic name accepted
            # as a fallback so an operator who set it under either namespace
            # resolves. (Operator-pass §2.7 fix.) Resolved once above.
            cloudflare_account_id=cloudflare_account_id,
        )
        resolved_backends.append(load_image_backend(slot_config))

    if not resolved_backends:
        # D-20-15 ALL-fail branch — fail-loud with operator-actionable context.
        raise TierNotConfiguredError(
            f"tier {_IMAGEGEN_TIER_NAME!r} has no resolvable backends",
            context={
                "tier": _IMAGEGEN_TIER_NAME,
                "configured_models": ",".join(f"{p}/{m}" for p, m in parsed),
                "missing_providers": ",".join(p for p, _ in skipped),
                "consulted_env_vars": ",".join(v for _, v in skipped if v),
            },
        )

    if len(resolved_backends) == 1:
        # Degenerate single-backend fast path — bypass wrapper overhead.
        return resolved_backends[0]

    # Wrap N≥2 in MultiModelImageBackend. Lazy import keeps the
    # :mod:`persona.imagegen` package importable for callers that don't
    # need the wrapper (parallel of the lazy concrete-backend imports above).
    from persona.imagegen.multi_model_image import MultiModelImageBackend

    return MultiModelImageBackend(
        backends=resolved_backends,
        tier_name=_IMAGEGEN_TIER_NAME,
    )
