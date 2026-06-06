"""Tier configuration and the backend registry (T03; D-05-3, D-05-4).

The router (T04) picks a tier name — ``"frontier"``, ``"mid"``, or
``"small"`` — and the :class:`TierRegistry` turns that into a concrete
:class:`persona.backends.ChatBackend`. Backends are instantiated lazily (on
first :meth:`TierRegistry.get`) and cached, because each holds a client
(``httpx`` for Ollama, an SDK client for hosted providers) that we don't want
to open for tiers a persona never uses.

Fallback (D-05-3): an unconfigured tier falls back ``small → mid → frontier``.
If no tiers are configured at all, the registry serves a single backend built
from the default ``PERSONA_*`` env (spec-02 ``BackendConfig`` defaults) for
every tier name. If nothing resolves, :class:`TierNotConfiguredError`.

Lifecycle (D-05-4): :meth:`aclose` disconnects every *cached* backend. The
**composition root** (the API in spec 08, or the integration-test fixtures)
owns this — the :class:`~persona_runtime.loop.ConversationLoop` uses the
registry but never closes it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from persona.backends import BackendConfig, load_backend
from persona.logging import get_logger

from persona_runtime.errors import TierNotConfiguredError

if TYPE_CHECKING:
    from persona.backends import ChatBackend

__all__ = ["TierConfig", "TierRegistry", "tier_registry_from_env"]

_logger = get_logger("runtime.tier")

# Resolution order for fallback when a requested tier is unconfigured (D-05-3).
_FALLBACK_ORDER: tuple[str, ...] = ("small", "mid", "frontier")

# The env-var prefixes the from-env builder reads (one per tier).
_TIER_ENV_PREFIXES: dict[str, str] = {
    "frontier": "PERSONA_FRONTIER_",
    "mid": "PERSONA_MID_",
    "small": "PERSONA_SMALL_",
}


@dataclass(frozen=True)
class TierConfig:
    """How to instantiate the backend for one tier (spec §6.2).

    Attributes:
        name: Tier name — ``"frontier"``, ``"mid"``, or ``"small"``.
        backend_config: The :class:`persona.backends.BackendConfig` passed to
            :func:`persona.backends.load_backend` on first use.
    """

    name: str
    backend_config: BackendConfig


class TierRegistry:
    """Resolves tier names to backends; lazy-instantiates and caches.

    Args:
        tiers: Mapping of tier name to :class:`TierConfig`. May be partial
            (only the tiers the deployment configured). An empty mapping is
            valid only if a single-backend fallback was baked in by the
            builder; otherwise :meth:`get` raises.
    """

    def __init__(self, tiers: dict[str, TierConfig]) -> None:
        self._tiers = dict(tiers)
        self._cache: dict[str, ChatBackend] = {}

    @property
    def configured_tier_names(self) -> tuple[str, ...]:
        """Names of every tier that has a :class:`TierConfig` registered.

        Insertion order is preserved so callers (e.g., the router's vision
        pre-filter, T13-T09) can produce stable error context strings.
        """
        return tuple(self._tiers)

    def supports_vision_for(self, tier_name: str) -> bool:
        """Whether the backend for ``tier_name`` accepts image content.

        Resolves through the same fallback chain as :meth:`get` (so a
        request for an unconfigured tier consults the fallback's backend),
        instantiates the backend lazily, and reads its ``supports_vision``
        capability. The router (T13-T09) consults this BEFORE any rule
        fires so that image-bearing turns can never land on a text-only
        tier.

        Args:
            tier_name: The tier name to inspect.

        Returns:
            ``True`` iff the resolved backend's ``supports_vision`` is
            ``True``; ``False`` if the backend is text-only.

        Raises:
            TierNotConfiguredError: No tier resolves, even after fallback.
        """
        # Defensive ``getattr``: the ChatBackend Protocol declares
        # ``supports_vision`` (D-13-X-error-hierarchy / T04), but a backend
        # built before that declaration may not expose it yet. Treat the
        # missing-attribute case as "not vision-capable" so the router's
        # pre-filter stays fail-loud on image turns without blowing up on
        # legacy backends.
        return bool(getattr(self.get(tier_name), "supports_vision", False))

    def get(self, tier_name: str) -> ChatBackend:
        """Return the backend for ``tier_name``, instantiating + caching once.

        Resolves the effective tier (applying the ``small → mid → frontier``
        fallback when ``tier_name`` is unconfigured), then returns the cached
        backend or builds it via :func:`load_backend` on first use.

        Args:
            tier_name: The tier the router chose.

        Returns:
            A :class:`ChatBackend`. The same instance is returned for repeated
            calls that resolve to the same effective tier.

        Raises:
            TierNotConfiguredError: No tier resolves, even after fallback.
        """
        effective = self._resolve(tier_name)
        if effective not in self._cache:
            self._cache[effective] = load_backend(self._tiers[effective].backend_config)
        return self._cache[effective]

    def _resolve(self, tier_name: str) -> str:
        """Return the configured tier name to use for ``tier_name``.

        Exact match wins. Otherwise walk the fallback order and return the
        first configured tier. Raise if nothing is configured.
        """
        if tier_name in self._tiers:
            return tier_name
        for candidate in _FALLBACK_ORDER:
            if candidate in self._tiers:
                _logger.warning(
                    "tier not configured; falling back requested={requested} using={using}",
                    requested=tier_name,
                    using=candidate,
                )
                return candidate
        raise TierNotConfiguredError(
            "no model tier is configured",
            context={
                "requested": tier_name,
                "configured": ", ".join(sorted(self._tiers)) or "(none)",
            },
        )

    async def aclose(self) -> None:
        """Disconnect every cached backend (D-05-4).

        Owned by the composition root, NOT the loop. Duck-types the cleanup:
        a backend exposing ``aclose`` or ``disconnect`` (e.g.,
        :class:`persona.backends.OllamaBackend`) is awaited; others are
        skipped. Never-``get()``-ed tiers are not instantiated just to close
        them. Idempotent — the cache is cleared after closing.
        """
        for name, backend in self._cache.items():
            closer = getattr(backend, "aclose", None) or getattr(backend, "disconnect", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception as exc:  # noqa: BLE001 — shutdown is best-effort; log and continue
                _logger.warning(
                    "error closing tier backend tier={tier} error={error}",
                    tier=name,
                    error=type(exc).__name__,
                )
        self._cache.clear()


def tier_registry_from_env(*, default_prefix: str = "PERSONA_") -> TierRegistry:
    """Build a :class:`TierRegistry` from the environment (D-05-3).

    For each tier, a ``TierConfig`` is added only if that tier's env block is
    present — detected by the ``<PREFIX>PROVIDER`` env var being set (a tier
    with no provider env var is treated as unconfigured, since
    :meth:`BackendConfig.from_env` would otherwise silently return field
    defaults). When no tier blocks are present, a single backend built from
    ``default_prefix`` (the spec-02 ``PERSONA_*`` defaults) is registered for
    all three tier names.

    Args:
        default_prefix: Env prefix for the single-backend fallback.

    Returns:
        A registry with whatever tiers the environment configured.
    """
    tiers: dict[str, TierConfig] = {}
    for tier_name, prefix in _TIER_ENV_PREFIXES.items():
        if f"{prefix}PROVIDER" in os.environ:
            tiers[tier_name] = TierConfig(
                name=tier_name,
                backend_config=BackendConfig.from_env(prefix=prefix),
            )

    if not tiers:
        single = BackendConfig.from_env(prefix=default_prefix)
        for tier_name in _TIER_ENV_PREFIXES:
            tiers[tier_name] = TierConfig(name=tier_name, backend_config=single)
        _logger.info(
            "no per-tier env configured; serving the default backend for all tiers "
            "provider={provider}",
            provider=single.provider,
        )

    return TierRegistry(tiers)
