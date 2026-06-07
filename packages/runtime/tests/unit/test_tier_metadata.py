"""Unit tests for the Spec 18 TierRegistry metadata extension (T04).

Covers :class:`TierMetadata` boundary discipline (frozen, ``extra="forbid"``,
field validation), :class:`TierConfig` back-compat (the new ``metadata`` field
defaults to ``None``), :meth:`TierRegistry.metadata_for` (lookup + fallback
chain + raises when nothing resolves), and :func:`tier_metadata_from_env`
(env-driven population — all six vars required; partial / malformed →
``None``).
"""

from __future__ import annotations

import pytest
from persona.backends import BackendConfig
from persona_runtime.errors import TierNotConfiguredError
from persona_runtime.tier import (
    TierConfig,
    TierMetadata,
    TierRegistry,
    tier_metadata_from_env,
    tier_registry_from_env,
)
from pydantic import ValidationError


def _dummy_backend_config() -> BackendConfig:
    return BackendConfig(provider="anthropic", model="claude-sonnet-4-6", api_key="sk-test")


def _full_metadata(
    *,
    cost_in: float = 0.3,
    cost_out: float = 1.5,
    latency_ms: float = 800.0,
    throughput: float = 60.0,
    context: int = 200000,
    tool: str = "strong",
) -> TierMetadata:
    return TierMetadata(
        cost_input_per_1k_tokens=cost_in,
        cost_output_per_1k_tokens=cost_out,
        first_token_latency_ms=latency_ms,
        throughput_tokens_per_sec=throughput,
        context_window=context,
        tool_strength=tool,  # type: ignore[arg-type]
    )


# ----- TierMetadata boundary --------------------------------------------------


class TestTierMetadataConstruction:
    def test_construction_with_all_fields(self) -> None:
        md = _full_metadata()
        assert md.cost_input_per_1k_tokens == pytest.approx(0.3)
        assert md.tool_strength == "strong"


class TestTierMetadataFrozen:
    def test_attribute_assignment_raises(self) -> None:
        md = _full_metadata()
        with pytest.raises(ValidationError):
            md.cost_input_per_1k_tokens = 99.0  # type: ignore[misc]


class TestTierMetadataExtraForbid:
    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TierMetadata(
                cost_input_per_1k_tokens=0.3,
                cost_output_per_1k_tokens=1.5,
                first_token_latency_ms=800.0,
                throughput_tokens_per_sec=60.0,
                context_window=200000,
                tool_strength="strong",
                undeclared="rogue",  # type: ignore[call-arg]
            )


class TestTierMetadataValidation:
    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _full_metadata(cost_in=-0.1)

    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _full_metadata(latency_ms=-1.0)

    def test_zero_context_window_rejected(self) -> None:
        # Field has gt=0; zero is invalid.
        with pytest.raises(ValidationError):
            _full_metadata(context=0)

    def test_invalid_tool_strength_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _full_metadata(tool="excellent")


# ----- TierConfig back-compat -------------------------------------------------


class TestTierConfigBackCompat:
    def test_construction_without_metadata_defaults_to_none(self) -> None:
        cfg = TierConfig(name="mid", backend_config=_dummy_backend_config())
        assert cfg.metadata is None

    def test_construction_with_metadata(self) -> None:
        md = _full_metadata()
        cfg = TierConfig(name="mid", backend_config=_dummy_backend_config(), metadata=md)
        assert cfg.metadata is md


# ----- TierRegistry.metadata_for ---------------------------------------------


class TestTierRegistryMetadataFor:
    def test_returns_metadata_when_populated(self) -> None:
        md = _full_metadata(latency_ms=600.0)
        registry = TierRegistry(
            {
                "frontier": TierConfig(
                    name="frontier",
                    backend_config=_dummy_backend_config(),
                    metadata=md,
                ),
            }
        )
        assert registry.metadata_for("frontier") is md
        assert registry.metadata_for("frontier").first_token_latency_ms == pytest.approx(600.0)

    def test_returns_none_when_metadata_unset(self) -> None:
        registry = TierRegistry(
            {
                "mid": TierConfig(name="mid", backend_config=_dummy_backend_config()),
            }
        )
        assert registry.metadata_for("mid") is None

    def test_walks_fallback_chain_for_unconfigured_tier(self) -> None:
        # Asking for "frontier" with only "mid" configured falls back to mid.
        md = _full_metadata()
        registry = TierRegistry(
            {
                "mid": TierConfig(
                    name="mid",
                    backend_config=_dummy_backend_config(),
                    metadata=md,
                ),
            }
        )
        # frontier resolves to mid via the fallback chain (small → mid → frontier
        # reversed for resolve); metadata follows.
        assert registry.metadata_for("frontier") is md

    def test_raises_when_no_tier_configured(self) -> None:
        registry = TierRegistry({})
        with pytest.raises(TierNotConfiguredError):
            registry.metadata_for("frontier")

    def test_does_not_instantiate_backend(self) -> None:
        # metadata_for is read-only — must NOT trigger load_backend.
        cfg = TierConfig(
            name="mid",
            backend_config=_dummy_backend_config(),
            metadata=_full_metadata(),
        )
        registry = TierRegistry({"mid": cfg})
        # Empty cache before the lookup; still empty after — proves no
        # backend was instantiated.
        assert registry._cache == {}  # noqa: SLF001 — verifying the no-side-effect contract
        registry.metadata_for("mid")
        assert registry._cache == {}  # noqa: SLF001


# ----- tier_metadata_from_env ------------------------------------------------


class TestTierMetadataFromEnv:
    def test_returns_metadata_when_all_six_vars_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PERSONA_FRONTIER_COST_INPUT_PER_1K", "0.3")
        monkeypatch.setenv("PERSONA_FRONTIER_COST_OUTPUT_PER_1K", "1.5")
        monkeypatch.setenv("PERSONA_FRONTIER_FIRST_TOKEN_LATENCY_MS", "800")
        monkeypatch.setenv("PERSONA_FRONTIER_THROUGHPUT_TOKENS_PER_SEC", "60")
        monkeypatch.setenv("PERSONA_FRONTIER_CONTEXT_WINDOW", "200000")
        monkeypatch.setenv("PERSONA_FRONTIER_TOOL_STRENGTH", "strong")
        md = tier_metadata_from_env(prefix="PERSONA_FRONTIER_")
        assert md is not None
        assert md.cost_input_per_1k_tokens == pytest.approx(0.3)
        assert md.context_window == 200000
        assert md.tool_strength == "strong"

    def test_returns_none_when_no_vars_present(self) -> None:
        # No monkeypatch — relying on no PERSONA_NEWTIER_* env vars existing.
        assert tier_metadata_from_env(prefix="PERSONA_NEWTIER_NONEXISTENT_") is None

    def test_returns_none_when_partial_vars_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 5/6 vars present — missing CONTEXT_WINDOW.
        monkeypatch.setenv("PERSONA_FRONTIER_COST_INPUT_PER_1K", "0.3")
        monkeypatch.setenv("PERSONA_FRONTIER_COST_OUTPUT_PER_1K", "1.5")
        monkeypatch.setenv("PERSONA_FRONTIER_FIRST_TOKEN_LATENCY_MS", "800")
        monkeypatch.setenv("PERSONA_FRONTIER_THROUGHPUT_TOKENS_PER_SEC", "60")
        monkeypatch.setenv("PERSONA_FRONTIER_TOOL_STRENGTH", "strong")
        assert tier_metadata_from_env(prefix="PERSONA_FRONTIER_") is None

    def test_returns_none_when_malformed_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_FRONTIER_COST_INPUT_PER_1K", "not-a-float")
        monkeypatch.setenv("PERSONA_FRONTIER_COST_OUTPUT_PER_1K", "1.5")
        monkeypatch.setenv("PERSONA_FRONTIER_FIRST_TOKEN_LATENCY_MS", "800")
        monkeypatch.setenv("PERSONA_FRONTIER_THROUGHPUT_TOKENS_PER_SEC", "60")
        monkeypatch.setenv("PERSONA_FRONTIER_CONTEXT_WINDOW", "200000")
        monkeypatch.setenv("PERSONA_FRONTIER_TOOL_STRENGTH", "strong")
        assert tier_metadata_from_env(prefix="PERSONA_FRONTIER_") is None


# ----- tier_registry_from_env extends to metadata ----------------------------


class TestTierRegistryFromEnvWithMetadata:
    def test_per_tier_metadata_populated_when_env_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PERSONA_FRONTIER_PROVIDER", "anthropic")
        monkeypatch.setenv("PERSONA_FRONTIER_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("PERSONA_FRONTIER_API_KEY", "sk-test")
        monkeypatch.setenv("PERSONA_FRONTIER_COST_INPUT_PER_1K", "1.5")
        monkeypatch.setenv("PERSONA_FRONTIER_COST_OUTPUT_PER_1K", "7.5")
        monkeypatch.setenv("PERSONA_FRONTIER_FIRST_TOKEN_LATENCY_MS", "1200")
        monkeypatch.setenv("PERSONA_FRONTIER_THROUGHPUT_TOKENS_PER_SEC", "40")
        monkeypatch.setenv("PERSONA_FRONTIER_CONTEXT_WINDOW", "200000")
        monkeypatch.setenv("PERSONA_FRONTIER_TOOL_STRENGTH", "strong")
        registry = tier_registry_from_env()
        md = registry.metadata_for("frontier")
        assert md is not None
        assert md.cost_input_per_1k_tokens == pytest.approx(1.5)
        assert md.context_window == 200000

    def test_metadata_none_when_env_partial(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_MID_PROVIDER", "anthropic")
        monkeypatch.setenv("PERSONA_MID_MODEL", "claude-haiku-4-5")
        monkeypatch.setenv("PERSONA_MID_API_KEY", "sk-test")
        # No metadata env vars — tier still configured, metadata is None.
        registry = tier_registry_from_env()
        assert registry.metadata_for("mid") is None
