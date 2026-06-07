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

Spec 18 (T04; D-18-3, D-18-X-protocol-location, D-18-X-latency-measurement-source):
adds optional per-tier :class:`TierMetadata` (cost / first-token latency /
throughput / context window / tool strength) at the **registry layer** —
NOT on the :class:`ChatBackend` Protocol. The router (Spec 18 ``UnifiedRouter``)
reads it; the backend never does. Existing :class:`TierConfig` constructions
stay valid (the metadata field defaults to ``None``); existing callers of
:func:`tier_registry_from_env` stay green (metadata population is opt-in via
new env vars).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from persona.backends import BackendConfig, load_backend
from persona.logging import get_logger
from pydantic import BaseModel, ConfigDict, Field

from persona_runtime.errors import TierNotConfiguredError

if TYPE_CHECKING:
    from persona.backends import ChatBackend

__all__ = [
    "TierConfig",
    "TierMetadata",
    "TierRegistry",
    "tier_metadata_from_env",
    "tier_registry_from_env",
]

_logger = get_logger("runtime.tier")

# Resolution order for fallback when a requested tier is unconfigured (D-05-3).
_FALLBACK_ORDER: tuple[str, ...] = ("small", "mid", "frontier")

# The env-var prefixes the from-env builder reads (one per tier).
_TIER_ENV_PREFIXES: dict[str, str] = {
    "frontier": "PERSONA_FRONTIER_",
    "mid": "PERSONA_MID_",
    "small": "PERSONA_SMALL_",
}


class TierMetadata(BaseModel):
    """Per-tier routing metadata (Spec 18 T04; D-18-3, D-18-X-protocol-location).

    Frozen Pydantic v2 + ``extra="forbid"``. Lives at the :class:`TierRegistry`
    layer, **NOT** on the :class:`~persona.backends.ChatBackend` Protocol
    (D-18-X-protocol-location). The :class:`UnifiedRouter` (Spec 18 T09–T11)
    reads it via :meth:`TierRegistry.metadata_for`; the backend never does.

    The :class:`HeuristicRouter` path (Spec 05 rules) does NOT consult metadata
    — it operates on context signals + capability matrices only. Metadata is
    populated, sparse, and additive; absence is handled per
    D-18-X-partial-metadata-behaviour (tiers without metadata are excluded
    from Layer 2 scoring; fall back to :class:`HeuristicRouter` if filtered
    set is empty).

    V5 R-V5-1 coordination: V5 reads :attr:`first_token_latency_ms` from this
    registry. One measurement, two consumers (D-18-X-latency-measurement-source).

    Attributes:
        cost_input_per_1k_tokens: Provider cost per 1k INPUT tokens (cents).
        cost_output_per_1k_tokens: Provider cost per 1k OUTPUT tokens (cents).
        first_token_latency_ms: Mean first-token latency (milliseconds). Seed
            from provider docs or env; the in-band
            :class:`FirstTokenLatencyTracker` (T06) updates the value via EWMA
            once warmed up.
        throughput_tokens_per_sec: Mean throughput (tokens/sec) after first
            token. Provider-documented or empirical.
        context_window: Maximum context window the backend supports (tokens).
            Drives Layer 1's context-window constraint when the turn's
            ``estimated_input_tokens`` exceeds the window.
        tool_strength: Categorical strength of the model's native tool-calling.
            Drives Layer 1's strong-tools constraint when the turn's
            ``requires_strong_tools`` is ``True``. ``"weak"`` excludes the tier
            in that case; ``"medium"`` / ``"strong"`` keep it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cost_input_per_1k_tokens: float = Field(ge=0.0)
    cost_output_per_1k_tokens: float = Field(ge=0.0)
    first_token_latency_ms: float = Field(ge=0.0)
    throughput_tokens_per_sec: float = Field(ge=0.0)
    context_window: int = Field(gt=0)
    tool_strength: Literal["weak", "medium", "strong"]


@dataclass(frozen=True)
class TierConfig:
    """How to instantiate the backend for one tier (spec §6.2).

    Spec 18 (T04) adds optional :attr:`metadata` carrying cost / first-token
    latency / throughput / context window / tool strength for the Spec 18
    :class:`UnifiedRouter`. Existing constructions stay valid — the field
    defaults to ``None`` (no metadata; :class:`UnifiedRouter` excludes the
    tier from Layer 2 scoring per D-18-X-partial-metadata-behaviour).

    Attributes:
        name: Tier name — ``"frontier"``, ``"mid"``, or ``"small"``.
        backend_config: The :class:`persona.backends.BackendConfig` passed to
            :func:`persona.backends.load_backend` on first use.
        metadata: Optional :class:`TierMetadata` for the Spec 18 router. When
            ``None``, the tier is invisible to :class:`UnifiedRouter`'s Layer 2
            scorer (the heuristic floor still serves the tier — fallback path).
    """

    name: str
    backend_config: BackendConfig
    metadata: TierMetadata | None = field(default=None)


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

    def model_name_for(self, tier_name: str) -> str:
        """Return the configured model name for ``tier_name`` without instantiating.

        Spec 18 (T05). Read-only — does NOT trigger backend instantiation.
        Used by :class:`HeuristicRouter.route` and :class:`UnifiedRouter.route`
        to populate :attr:`RoutingDecision.model` without forcing eager
        client construction.

        Args:
            tier_name: The tier name to look up.

        Returns:
            The configured model name string (e.g. ``"claude-sonnet-4-6"``).
            Resolves through the same fallback chain as :meth:`get`.

        Raises:
            TierNotConfiguredError: No tier resolves, even after fallback.
        """
        effective = self._resolve(tier_name)
        return self._tiers[effective].backend_config.model

    def metadata_for(self, tier_name: str) -> TierMetadata | None:
        """Return :class:`TierMetadata` for ``tier_name``, or ``None`` if unset.

        Spec 18 (T04). Resolves through the same fallback chain as :meth:`get`,
        so a request for an unconfigured tier consults the fallback's metadata
        — but unlike :meth:`get`, this method does NOT instantiate the backend
        (metadata lookup is read-only and never triggers backend construction).

        Args:
            tier_name: The tier name to look up.

        Returns:
            The :class:`TierMetadata` instance configured for the effective
            tier, or ``None`` when neither the requested tier nor its fallback
            has metadata populated. ``None`` is the signal Spec 18's
            ``UnifiedRouter`` reads to exclude the tier from Layer 2 scoring
            (D-18-X-partial-metadata-behaviour); the heuristic floor still
            serves the tier on the fallback path (D-18-4).

        Raises:
            TierNotConfiguredError: No tier resolves, even after fallback.
        """
        effective = self._resolve(tier_name)
        return self._tiers[effective].metadata

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


def tier_metadata_from_env(*, prefix: str) -> TierMetadata | None:
    """Build :class:`TierMetadata` from per-tier env vars (Spec 18 T04).

    All six fields must be present (and well-formed) for a non-``None``
    return — partial env configuration returns ``None`` and is logged at
    DEBUG. This is the operator-override path; the in-band
    :class:`FirstTokenLatencyTracker` (T06) is the measured path that
    supersedes the env value once warmed up.

    Env var schema (per tier ``PREFIX`` matching the tier's
    :attr:`BackendConfig` prefix, e.g. ``PERSONA_FRONTIER_``):

    * ``<PREFIX>COST_INPUT_PER_1K`` (float, cents)
    * ``<PREFIX>COST_OUTPUT_PER_1K`` (float, cents)
    * ``<PREFIX>FIRST_TOKEN_LATENCY_MS`` (float, milliseconds)
    * ``<PREFIX>THROUGHPUT_TOKENS_PER_SEC`` (float)
    * ``<PREFIX>CONTEXT_WINDOW`` (int, tokens)
    * ``<PREFIX>TOOL_STRENGTH`` (one of ``"weak"`` / ``"medium"`` /
      ``"strong"``)

    Args:
        prefix: The tier's env-var prefix (e.g. ``"PERSONA_FRONTIER_"``).

    Returns:
        A :class:`TierMetadata` instance when all six env vars are present
        and parse cleanly; ``None`` otherwise (partial / absent / malformed).
    """
    required_keys = (
        f"{prefix}COST_INPUT_PER_1K",
        f"{prefix}COST_OUTPUT_PER_1K",
        f"{prefix}FIRST_TOKEN_LATENCY_MS",
        f"{prefix}THROUGHPUT_TOKENS_PER_SEC",
        f"{prefix}CONTEXT_WINDOW",
        f"{prefix}TOOL_STRENGTH",
    )
    if not all(key in os.environ for key in required_keys):
        return None
    try:
        return TierMetadata(
            cost_input_per_1k_tokens=float(os.environ[required_keys[0]]),
            cost_output_per_1k_tokens=float(os.environ[required_keys[1]]),
            first_token_latency_ms=float(os.environ[required_keys[2]]),
            throughput_tokens_per_sec=float(os.environ[required_keys[3]]),
            context_window=int(os.environ[required_keys[4]]),
            tool_strength=os.environ[required_keys[5]],  # type: ignore[arg-type]
        )
    except (ValueError, TypeError) as exc:
        _logger.warning(
            "malformed tier metadata env vars; metadata absent prefix={prefix} error={error}",
            prefix=prefix,
            error=type(exc).__name__,
        )
        return None


def tier_registry_from_env(*, default_prefix: str = "PERSONA_") -> TierRegistry:
    """Build a :class:`TierRegistry` from the environment (D-05-3).

    For each tier, a ``TierConfig`` is added only if that tier's env block is
    present — detected by the ``<PREFIX>PROVIDER`` env var being set (a tier
    with no provider env var is treated as unconfigured, since
    :meth:`BackendConfig.from_env` would otherwise silently return field
    defaults). When no tier blocks are present, a single backend built from
    ``default_prefix`` (the spec-02 ``PERSONA_*`` defaults) is registered for
    all three tier names.

    Spec 18 (T04): per-tier :class:`TierMetadata` is populated additively via
    :func:`tier_metadata_from_env` when the tier's six metadata env vars are
    all present. Absent / partial metadata is silently accepted (returns
    ``None``); the :class:`UnifiedRouter` excludes the tier from Layer 2
    scoring while :class:`HeuristicRouter` continues to serve it.

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
                metadata=tier_metadata_from_env(prefix=prefix),
            )

    if not tiers:
        single = BackendConfig.from_env(prefix=default_prefix)
        single_metadata = tier_metadata_from_env(prefix=default_prefix)
        for tier_name in _TIER_ENV_PREFIXES:
            tiers[tier_name] = TierConfig(
                name=tier_name,
                backend_config=single,
                metadata=single_metadata,
            )
        _logger.info(
            "no per-tier env configured; serving the default backend for all tiers "
            "provider={provider}",
            provider=single.provider,
        )

    return TierRegistry(tiers)
