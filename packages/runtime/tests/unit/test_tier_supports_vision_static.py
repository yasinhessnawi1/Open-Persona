"""R1-D-1 — ``TierRegistry.supports_vision_for`` is static; never instantiates.

The community read paths (persona create / get / list) hydrate
``PersonaCapabilities`` by asking the tier registry whether a tier supports
vision. That answer is a static ``(provider, model)`` fact and must NOT require
a live model backend — a keyless community boot 500'd because the old code
called ``get() → load_backend()`` and the backend constructor fail-fasts on the
missing key.

New contract:
  * already-built backend (preconstructed / cached) → read its ``.supports_vision``
    property (the cloud-unchanged path);
  * else → compute statically from ``backend_config.(provider, model)``;
  * NEVER call ``load_backend()`` on a read.

These tests prove BOTH legs — the keyless fix AND the cloud-byte-unchanged
guarantee (a preconstructed backend's property is read identically to before,
and the router's vision pre-filter receives the same bool).
"""

from __future__ import annotations

import pytest
from persona.backends import BackendConfig
from persona_runtime.tier import TierConfig, TierRegistry


class _ScriptedBackend:
    """Minimal ChatBackend stand-in exposing only ``supports_vision``."""

    def __init__(self, *, supports_vision: bool) -> None:
        self._sv = supports_vision

    @property
    def supports_vision(self) -> bool:
        return self._sv


def _cfg(provider: str, model: str) -> BackendConfig:
    # api_key=None ⇒ a keyless config: load_backend() on this would 500.
    return BackendConfig(provider=provider, model=model, api_key=None)  # type: ignore[arg-type]


def _keyless_registry(tiers: dict[str, tuple[str, str]]) -> TierRegistry:
    """Registry with NO preconstructed backends and an EMPTY cache.

    The old ``supports_vision_for`` would ``load_backend`` these on read and
    raise ``AuthenticationError`` (missing key). The new one answers statically.
    """
    return TierRegistry(
        {name: TierConfig(name=name, backend_config=_cfg(p, m)) for name, (p, m) in tiers.items()}
    )


# ----- the keyless fix -----------------------------------------------------


def test_keyless_vision_capable_tier_is_true_without_instantiating() -> None:
    registry = _keyless_registry({"frontier": ("anthropic", "claude-sonnet-4-6")})
    assert registry.supports_vision_for("frontier") is True


def test_keyless_text_only_tier_is_false_without_instantiating() -> None:
    registry = _keyless_registry({"small": ("deepseek", "deepseek-chat")})
    assert registry.supports_vision_for("small") is False


def test_supports_vision_for_never_calls_load_backend_on_a_keyless_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The byte-unchanged guarantee's foundation: reads never instantiate.

    Detonate ``load_backend`` so any instantiation attempt fails loudly.
    """
    import persona_runtime.tier as tier_mod

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("supports_vision_for must not call load_backend() on a read")

    monkeypatch.setattr(tier_mod, "load_backend", _boom)

    registry = _keyless_registry(
        {"frontier": ("anthropic", "claude-sonnet-4-6"), "small": ("deepseek", "deepseek-chat")}
    )
    assert registry.supports_vision_for("frontier") is True
    assert registry.supports_vision_for("small") is False


# ----- cloud-byte-unchanged guards -----------------------------------------


@pytest.mark.parametrize("vision", [True, False])
def test_preconstructed_backend_property_is_read_identically(vision: bool) -> None:
    """A built backend's ``.supports_vision`` is read as-is (cloud unchanged)."""
    registry = TierRegistry(
        {
            "frontier": TierConfig(
                name="frontier",
                backend_config=_cfg("anthropic", "m"),
                preconstructed_backend=_ScriptedBackend(supports_vision=vision),  # type: ignore[arg-type]
            )
        }
    )
    assert registry.supports_vision_for("frontier") is vision


def test_fallback_chain_resolution_is_preserved() -> None:
    """An unconfigured tier resolves through the fallback chain, statically."""
    # Only "frontier" configured; "small" must fall back to it (vision-capable).
    registry = _keyless_registry({"frontier": ("anthropic", "claude-sonnet-4-6")})
    assert registry.supports_vision_for("small") is True


def test_router_vision_prefilter_receives_the_same_bool_keyless() -> None:
    """The layer-1 vision pre-filter expression (layer1.py:76) over a keyless
    registry keeps vision-capable tiers and drops text-only ones — proving the
    router consumes the correct static bool without instantiation.
    """
    registry = _keyless_registry(
        {"frontier": ("anthropic", "claude-sonnet-4-6"), "small": ("deepseek", "deepseek-chat")}
    )
    configured = ("frontier", "small")
    vision_capable = tuple(t for t in configured if registry.supports_vision_for(t))
    assert vision_capable == ("frontier",)
