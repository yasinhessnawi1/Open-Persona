"""Unit tests for the Spec 20 T17 TierRegistry MODELS-list wiring.

Covers D-20-17 four cases (a/b/c/d) × per-tier resolution + D-20-15 per-slot
disposition (≥1 resolves → wrapper-with-remaining; ALL fail →
:class:`TierNotConfiguredError` with operator-actionable context) + the
partial-triplet ``IncompleteTierConfigError`` branch.

Backward-compat acceptance 5d preserved: pre-Spec-20 single-backend triplet
construction continues to produce a bare backend (no wrapper) and remains
indistinguishable from the legacy ``tier_registry_from_env`` shape.
"""

# ruff: noqa: SLF001, N801, ARG002 — tests reach into _resolve / wrapper
# internals, name TestCaseA_..D_.. classes after D-20-17 cases, and accept
# fixtures (``patched_load``, ``stub_image_backends``) solely for their
# monkey-patch side-effect (matches Spec 05 test convention at
# packages/runtime/tests/unit/test_tier.py).

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from loguru import logger as _loguru_logger
from persona.backends import BackendConfig
from persona.backends.errors import (
    IncompleteTierConfigError,
    MalformedTierModelsError,
    TierNotConfiguredError,
)
from persona.backends.multi_model import MultiModelChatBackend
from persona_runtime.tier import (
    TierRegistry,
    tier_registry_from_env,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class _FakeBackend:
    """Minimal ChatBackend stand-in capturing the BackendConfig it was built from."""

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.provider_name = config.provider
        self.model_name = config.model
        self.supports_native_tools = False
        self.supports_vision = False


@pytest.fixture
def patched_load(monkeypatch: pytest.MonkeyPatch) -> list[BackendConfig]:
    """Patch ``persona_runtime.tier.load_backend`` to capture per-slot configs.

    Returns the list of :class:`BackendConfig` instances that the registry
    constructed (one per resolved MODELS-list slot OR one per legacy triplet
    tier). Restoration handled by ``monkeypatch`` teardown.
    """
    seen: list[BackendConfig] = []

    def fake_load(config: BackendConfig) -> _FakeBackend:
        seen.append(config)
        return _FakeBackend(config)

    monkeypatch.setattr("persona_runtime.tier.load_backend", fake_load)
    return seen


@pytest.fixture
def loguru_capture() -> Iterator[list[str]]:
    """Loguru sink that captures every emitted message string.

    The project's logging surface (``persona.logging.get_logger``) wraps
    loguru, so pytest's stdlib-only ``caplog`` does not see records. This
    fixture mirrors the pattern used by Spec 20 T15's
    ``test_multi_model_chat.py``.
    """
    captured: list[str] = []
    sink_id = _loguru_logger.add(
        lambda msg: captured.append(str(msg)),
        level="INFO",
    )
    try:
        yield captured
    finally:
        _loguru_logger.remove(sink_id)


def _clear_tier_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every PERSONA_<TIER>_* env var so each test starts from a clean slate."""
    for tier in ("FRONTIER", "MID", "SMALL"):
        for suffix in ("MODELS", "PROVIDER", "MODEL", "API_KEY"):
            monkeypatch.delenv(f"PERSONA_{tier}_{suffix}", raising=False)
    for provider in ("ANTHROPIC", "OPENAI", "DEEPSEEK", "GROQ", "TOGETHER", "NVIDIA"):
        monkeypatch.delenv(f"PERSONA_{provider}_API_KEY", raising=False)
        monkeypatch.delenv(f"PERSONA_{provider}_BASE_URL", raising=False)
    for default in ("PERSONA_PROVIDER", "PERSONA_MODEL", "PERSONA_API_KEY"):
        monkeypatch.delenv(default, raising=False)


# --------------------------------------------------------------------------- #
# D-20-17 case (a) — MODELS-only set
# --------------------------------------------------------------------------- #


class TestCaseA_ModelsOnly:
    """D-20-17 (a): MODELS set, triplet UNSET → MODELS wins, no log."""

    def test_models_only_builds_wrapper_with_two_backends(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv(
            "PERSONA_FRONTIER_MODELS",
            "anthropic/claude-sonnet-4-6,openai/gpt-4o",
        )
        monkeypatch.setenv("PERSONA_ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("PERSONA_OPENAI_API_KEY", "sk-openai-test")

        reg = tier_registry_from_env()
        backend = reg.get("frontier")
        assert isinstance(backend, MultiModelChatBackend)
        # Two BackendConfigs constructed — one per resolved slot.
        assert len(patched_load) == 2
        assert [c.provider for c in patched_load] == ["anthropic", "openai"]
        assert [c.model for c in patched_load] == ["claude-sonnet-4-6", "gpt-4o"]

    def test_models_only_length_one_returns_bare_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        """D-20-17 (a) length-1 fast path: no wrapper overhead."""
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv("PERSONA_MID_MODELS", "anthropic/claude-sonnet-4-6")
        monkeypatch.setenv("PERSONA_ANTHROPIC_API_KEY", "sk-ant-test")

        reg = tier_registry_from_env()
        backend = reg.get("mid")
        assert not isinstance(backend, MultiModelChatBackend)
        assert backend.provider_name == "anthropic"
        assert backend.model_name == "claude-sonnet-4-6"

    def test_models_only_no_log_when_triplet_absent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
        loguru_capture: list[str],
    ) -> None:
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv("PERSONA_SMALL_MODELS", "groq/llama-3.1-8b-instant")
        monkeypatch.setenv("PERSONA_GROQ_API_KEY", "gsk-test")

        tier_registry_from_env()
        case_c_msgs = [msg for msg in loguru_capture if "case (c)" in msg]
        assert case_c_msgs == []


# --------------------------------------------------------------------------- #
# D-20-17 case (b) — Triplet-only (backward-compat)
# --------------------------------------------------------------------------- #


class TestCaseB_TripletOnly:
    """D-20-17 (b): all three triplet vars set, MODELS absent → backward-compat."""

    def test_triplet_only_builds_bare_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv("PERSONA_FRONTIER_PROVIDER", "anthropic")
        monkeypatch.setenv("PERSONA_FRONTIER_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("PERSONA_FRONTIER_API_KEY", "sk-ant-test")

        reg = tier_registry_from_env()
        backend = reg.get("frontier")
        assert not isinstance(backend, MultiModelChatBackend)
        assert backend.provider_name == "anthropic"
        # Legacy lazy path: load_backend called exactly once on `.get()`.
        assert len(patched_load) == 1


# --------------------------------------------------------------------------- #
# D-20-17 case (c) — BOTH set: MODELS wins + INFO log
# --------------------------------------------------------------------------- #


class TestCaseC_BothSet:
    """D-20-17 (c): MODELS + triplet both set → MODELS wins; INFO log fires."""

    def test_both_set_models_wins_and_info_log_emitted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
        loguru_capture: list[str],
    ) -> None:
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv(
            "PERSONA_FRONTIER_MODELS",
            "anthropic/claude-sonnet-4-6,openai/gpt-4o",
        )
        monkeypatch.setenv("PERSONA_FRONTIER_PROVIDER", "deepseek")
        monkeypatch.setenv("PERSONA_FRONTIER_MODEL", "deepseek-chat")
        monkeypatch.setenv("PERSONA_FRONTIER_API_KEY", "sk-deepseek-test")
        monkeypatch.setenv("PERSONA_ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("PERSONA_OPENAI_API_KEY", "sk-openai-test")

        reg = tier_registry_from_env()
        backend = reg.get("frontier")

        # MODELS wins — wrapper present with two slots, NOT deepseek/deepseek-chat.
        assert isinstance(backend, MultiModelChatBackend)
        assert [c.provider for c in patched_load] == ["anthropic", "openai"]

        # INFO log identifies the ignored triplet vars.
        case_c_msgs = [msg for msg in loguru_capture if "case (c)" in msg]
        assert len(case_c_msgs) == 1
        msg = case_c_msgs[0]
        assert "PERSONA_FRONTIER_API_KEY" in msg
        assert "PERSONA_FRONTIER_MODEL" in msg
        assert "PERSONA_FRONTIER_PROVIDER" in msg


# --------------------------------------------------------------------------- #
# D-20-17 case (d) — Malformed MODELS
# --------------------------------------------------------------------------- #


class TestCaseD_Malformed:
    """D-20-17 (d): malformed MODELS → MalformedTierModelsError propagates."""

    def test_missing_slash_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv("PERSONA_FRONTIER_MODELS", "anthropic-claude-sonnet-4-6")

        with pytest.raises(MalformedTierModelsError) as exc_info:
            tier_registry_from_env()
        assert exc_info.value.context["reason"] == "missing_slash"
        assert exc_info.value.context["tier"] == "frontier"

    def test_unknown_provider_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv("PERSONA_FRONTIER_MODELS", "made_up_provider/model-x")

        with pytest.raises(MalformedTierModelsError) as exc_info:
            tier_registry_from_env()
        assert exc_info.value.context["reason"] == "unknown_provider"


# --------------------------------------------------------------------------- #
# Partial-triplet branch (1-2 of 3 set + no MODELS)
# --------------------------------------------------------------------------- #


class TestPartialTriplet:
    """Triplet partial-set (no MODELS) → IncompleteTierConfigError."""

    def test_only_provider_set_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv("PERSONA_FRONTIER_PROVIDER", "anthropic")
        # MODEL + API_KEY intentionally unset.

        with pytest.raises(IncompleteTierConfigError) as exc_info:
            tier_registry_from_env()
        ctx = exc_info.value.context
        assert ctx["tier"] == "frontier"
        assert "PERSONA_FRONTIER_MODEL" in ctx["missing_vars"]
        assert "PERSONA_FRONTIER_API_KEY" in ctx["missing_vars"]


# --------------------------------------------------------------------------- #
# D-20-15 per-slot disposition (≥1 resolves vs ALL fail)
# --------------------------------------------------------------------------- #


class TestD20_15Disposition:
    """D-20-15 three-tier disposition at construction-time."""

    def test_two_of_three_missing_credentials_builds_with_remaining(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
        loguru_capture: list[str],
    ) -> None:
        """≥1 provider resolves → WARN per missing + wrapper with remaining slot(s)."""
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv(
            "PERSONA_FRONTIER_MODELS",
            "anthropic/claude-sonnet-4-6,openai/gpt-4o,deepseek/deepseek-chat",
        )
        # Only DeepSeek key set; anthropic + openai keys absent.
        monkeypatch.setenv("PERSONA_DEEPSEEK_API_KEY", "sk-deepseek-test")

        reg = tier_registry_from_env()
        backend = reg.get("frontier")

        # Only one slot resolved → bare-single fast path (no wrapper).
        assert not isinstance(backend, MultiModelChatBackend)
        assert backend.provider_name == "deepseek"

        warns = [msg for msg in loguru_capture if "credential missing" in msg]
        assert len(warns) == 2
        warn_text = " ".join(warns)
        assert "PERSONA_ANTHROPIC_API_KEY" in warn_text
        assert "PERSONA_OPENAI_API_KEY" in warn_text

    def test_all_providers_missing_credentials_raises_tier_not_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        """ALL fail → TierNotConfiguredError with missing_providers + consulted_env_vars."""
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv(
            "PERSONA_FRONTIER_MODELS",
            "anthropic/claude-sonnet-4-6,openai/gpt-4o",
        )
        # No keys at all → all slots fail.

        with pytest.raises(TierNotConfiguredError) as exc_info:
            tier_registry_from_env()
        ctx = exc_info.value.context
        assert ctx["tier"] == "frontier"
        assert "anthropic/claude-sonnet-4-6" in ctx["configured_models"]
        assert "openai/gpt-4o" in ctx["configured_models"]
        assert "anthropic" in ctx["missing_providers"]
        assert "openai" in ctx["missing_providers"]
        assert "PERSONA_ANTHROPIC_API_KEY" in ctx["consulted_env_vars"]
        assert "PERSONA_OPENAI_API_KEY" in ctx["consulted_env_vars"]


# --------------------------------------------------------------------------- #
# Mixed tiers — different precedence per tier in one registry build
# --------------------------------------------------------------------------- #


class TestMixedTiers:
    """Distinct cases across tiers compose correctly in one registry build."""

    def test_frontier_models_mid_triplet_small_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        _clear_tier_env(monkeypatch)
        # FRONTIER: case (a) MODELS.
        monkeypatch.setenv(
            "PERSONA_FRONTIER_MODELS",
            "anthropic/claude-sonnet-4-6,openai/gpt-4o",
        )
        monkeypatch.setenv("PERSONA_ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("PERSONA_OPENAI_API_KEY", "sk-openai-test")
        # MID: case (b) triplet.
        monkeypatch.setenv("PERSONA_MID_PROVIDER", "deepseek")
        monkeypatch.setenv("PERSONA_MID_MODEL", "deepseek-chat")
        monkeypatch.setenv("PERSONA_MID_API_KEY", "sk-deepseek-test")
        # SMALL: unset; falls back to MID via Spec 05 chain.

        reg = tier_registry_from_env()
        # Frontier → wrapper.
        assert isinstance(reg.get("frontier"), MultiModelChatBackend)
        # Mid → bare.
        mid = reg.get("mid")
        assert not isinstance(mid, MultiModelChatBackend)
        assert mid.provider_name == "deepseek"
        # Small → falls back to mid (Spec 05 D-05-3).
        assert reg.get("small") is mid


# --------------------------------------------------------------------------- #
# TierConfig.preconstructed_backend / cache interaction
# --------------------------------------------------------------------------- #


class TestPreconstructedBackend:
    """Construction-time cache seeding for MODELS-list tiers."""

    def test_preconstructed_backend_returned_without_load_backend_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Manually-constructed TierConfig with preconstructed_backend skips load_backend."""
        from persona_runtime.tier import TierConfig

        sentinel: Any = object()
        # If load_backend is called, the test fails loud.
        monkeypatch.setattr(
            "persona_runtime.tier.load_backend",
            lambda _config: pytest.fail("load_backend should NOT be called"),
        )
        cfg = TierConfig(
            name="frontier",
            backend_config=BackendConfig(provider="anthropic", model="m"),
            preconstructed_backend=sentinel,
        )
        reg = TierRegistry({"frontier": cfg})
        assert reg.get("frontier") is sentinel


# --------------------------------------------------------------------------- #
# Spec 22 D-22-2 — OpenRouter free-mode MODELS filter (T14)
# --------------------------------------------------------------------------- #


class TestOpenRouterFreeModeFilter:
    """``tier_registry_from_env(openrouter_subscription_mode=...)`` applies the
    D-22-2 free-mode filter: non-:free openrouter entries drop; a fully-emptied
    tier is left unregistered (fail-soft)."""

    def test_free_mode_drops_non_free_keeps_free_and_other_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv(
            "PERSONA_FRONTIER_MODELS",
            "openrouter/anthropic/claude-3.5-sonnet,"  # non-:free → dropped
            "openrouter/meta-llama/llama-3.3-70b-instruct:free,"  # :free → kept
            "nvidia/nemotron-3-super-120b-a12b",  # non-openrouter → kept
        )
        monkeypatch.setenv("PERSONA_OPENROUTER_API_KEY", "sk-or-v1-test")
        monkeypatch.setenv("PERSONA_NVIDIA_API_KEY", "nvapi-test")

        reg = tier_registry_from_env(openrouter_subscription_mode="free")
        assert isinstance(reg.get("frontier"), MultiModelChatBackend)
        # Two slots survived the filter — the :free openrouter + nvidia.
        assert [c.provider for c in patched_load] == ["openrouter", "nvidia"]
        # The parser splits on the FIRST slash, so the nvidia model carries no
        # "nvidia/" prefix; the openrouter model keeps its full vendor/model slug.
        assert [c.model for c in patched_load] == [
            "meta-llama/llama-3.3-70b-instruct:free",
            "nemotron-3-super-120b-a12b",
        ]

    def test_paid_mode_keeps_non_free_openrouter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv("PERSONA_MID_MODELS", "openrouter/anthropic/claude-3.5-sonnet")
        monkeypatch.setenv("PERSONA_OPENROUTER_API_KEY", "sk-or-v1-test")

        reg = tier_registry_from_env(openrouter_subscription_mode="paid")
        backend = reg.get("mid")
        assert backend.provider_name == "openrouter"
        assert backend.model_name == "anthropic/claude-3.5-sonnet"

    def test_free_mode_emptied_tier_is_unregistered(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_load: list[BackendConfig],
    ) -> None:
        # MID is all-non-:free openrouter → drops away; FRONTIER keeps the
        # registry non-empty so the default-for-all fallback does NOT fire.
        _clear_tier_env(monkeypatch)
        monkeypatch.setenv("PERSONA_FRONTIER_MODELS", "nvidia/nemotron-3-super-120b-a12b")
        monkeypatch.setenv("PERSONA_MID_MODELS", "openrouter/anthropic/claude-3.5-sonnet")
        monkeypatch.setenv("PERSONA_OPENROUTER_API_KEY", "sk-or-v1-test")
        monkeypatch.setenv("PERSONA_NVIDIA_API_KEY", "nvapi-test")

        reg = tier_registry_from_env(openrouter_subscription_mode="free")
        assert "frontier" in reg.configured_tier_names
        assert "mid" not in reg.configured_tier_names  # fail-soft drop
