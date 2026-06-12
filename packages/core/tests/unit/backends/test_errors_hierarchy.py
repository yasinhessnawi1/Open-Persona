"""Tests cementing the D-20-16 error-class hierarchy partition.

These tests fail if a future amendment accidentally reparents a wrapper-
layer or configuration-layer error class to :class:`ProviderError` instead
of :class:`PersonaError`. They also cover the ``except ProviderError``
filtering behaviour application code relies on per D-20-16.

The partition under test (Spec 20 D-20-16 — settled):

* Provider-layer (rooted at :class:`ProviderError`): HTTP/SDK failures
  the :class:`MultiModelChatBackend` classifier buckets per D-20-9.
* Wrapper / config-layer (rooted at :class:`PersonaError` directly):
  composition-layer failures that fail-loud at the application boundary.
"""

from __future__ import annotations

import pytest
from persona.backends.errors import (
    AllModelsFailedError,
    AuthenticationError,
    BackendTimeoutError,
    IncompleteTierConfigError,
    LocalProviderInModelsListError,
    MalformedTierModelsError,
    ModelNotFoundError,
    OpenRouterBalanceProbeError,
    OpenRouterCatalogError,
    ProviderCredentialMissingError,
    ProviderError,
    RateLimitError,
    TierNotConfiguredError,
)
from persona.errors import PersonaError

PROVIDER_LAYER: list[type[ProviderError]] = [
    AuthenticationError,
    BackendTimeoutError,
    ModelNotFoundError,
    RateLimitError,
    # Spec 22 D-22-1 + D-22-3: OpenRouter catalog/probe failures are
    # provider-layer (live HTTP calls), so they root at ProviderError.
    OpenRouterCatalogError,
    OpenRouterBalanceProbeError,
    # ``ProviderError`` itself excluded — it IS the partition root.
]

WRAPPER_OR_CONFIG_LAYER: list[type[PersonaError]] = [
    AllModelsFailedError,
    IncompleteTierConfigError,
    LocalProviderInModelsListError,
    MalformedTierModelsError,
    ProviderCredentialMissingError,
    TierNotConfiguredError,
]


class TestProviderLayerPartition:
    """Provider-layer classes root at :class:`ProviderError` (D-20-16)."""

    @pytest.mark.parametrize("cls", PROVIDER_LAYER)
    def test_provider_layer_class_inherits_from_provider_error(
        self, cls: type[ProviderError]
    ) -> None:
        assert issubclass(cls, ProviderError), (
            f"{cls.__name__} must inherit from ProviderError per D-20-16 "
            "provider-layer partition; backends raise these for HTTP/SDK failures."
        )

    @pytest.mark.parametrize("cls", PROVIDER_LAYER)
    def test_provider_layer_class_inherits_from_persona_error(
        self, cls: type[ProviderError]
    ) -> None:
        # Provider-layer is also a PersonaError (via ProviderError chain).
        assert issubclass(cls, PersonaError)


class TestWrapperConfigPartition:
    """Wrapper / config-layer classes root at :class:`PersonaError` directly,
    NOT under :class:`ProviderError` (D-20-16)."""

    @pytest.mark.parametrize("cls", WRAPPER_OR_CONFIG_LAYER)
    def test_wrapper_config_class_inherits_from_persona_error_directly(
        self, cls: type[PersonaError]
    ) -> None:
        assert issubclass(cls, PersonaError), (
            f"{cls.__name__} must inherit from PersonaError per D-20-16."
        )
        assert not issubclass(cls, ProviderError), (
            f"{cls.__name__} must NOT inherit from ProviderError per D-20-16 "
            "wrapper-layer partition. Composition-layer failures fail-loud "
            "at the application layer; they are not provider-side errors."
        )


class TestExceptProviderErrorFiltering:
    """Application-code contract: ``except ProviderError`` catches HTTP/SDK
    failures but NOT wrapper/config failures. Cementing this so future code
    can rely on it (D-20-16)."""

    @pytest.mark.parametrize("cls", PROVIDER_LAYER)
    def test_provider_layer_caught_by_except_provider_error(self, cls: type[ProviderError]) -> None:
        try:
            raise cls("smoke", context={"provider": "test"})
        except ProviderError:
            return
        pytest.fail(f"{cls.__name__} should be caught by 'except ProviderError'")

    @pytest.mark.parametrize("cls", WRAPPER_OR_CONFIG_LAYER)
    def test_wrapper_config_not_caught_by_except_provider_error(
        self, cls: type[PersonaError]
    ) -> None:
        try:
            raise cls("smoke", context={"tier": "test"})
        except ProviderError:
            pytest.fail(
                f"{cls.__name__} must NOT be caught by 'except ProviderError' "
                "per D-20-16; wrapper/config errors should reach application layer."
            )
        except PersonaError:
            return


class TestContextShape:
    """Each Spec-20 wrapper/config error class accepts a
    ``context: dict[str, str]`` keyword arg per the :class:`PersonaError`
    convention. Smoke-test the canonical context shapes documented on each
    class (D-20-15 / D-20-17 / D-20-18)."""

    def test_all_models_failed_error_context_keys(self) -> None:
        exc = AllModelsFailedError(
            "all backends exhausted",
            context={
                "tier": "frontier",
                "attempt_count": "3",
                "attempts_json": "[{...}]",
                "final_error_class": "RateLimitError",
            },
        )
        assert exc.context["tier"] == "frontier"
        assert exc.context["attempt_count"] == "3"
        assert exc.context["final_error_class"] == "RateLimitError"
        assert "tier=frontier" in str(exc)

    def test_provider_credential_missing_error_context_keys(self) -> None:
        # D-20-15 canonical shape.
        exc = ProviderCredentialMissingError(
            "missing key",
            context={"provider": "nvidia", "env_var": "PERSONA_NVIDIA_API_KEY"},
        )
        assert exc.context["provider"] == "nvidia"
        assert exc.context["env_var"] == "PERSONA_NVIDIA_API_KEY"

    def test_local_provider_in_models_list_error_context_keys(self) -> None:
        # D-20-18 canonical shape.
        exc = LocalProviderInModelsListError(
            "local rejected",
            context={
                "tier": "frontier",
                "position": "1",
                "hint": "use PERSONA_LOCAL_MODEL_ID for in-process HF weights",
            },
        )
        assert exc.context["tier"] == "frontier"
        assert exc.context["position"] == "1"
        assert "hint" in exc.context

    def test_malformed_tier_models_error_context_keys(self) -> None:
        # D-20-17 case (d) canonical shape.
        exc = MalformedTierModelsError(
            "parse failure",
            context={
                "tier": "mid",
                "value": "openai/",
                "reason": "empty_model",
                "position": "0",
            },
        )
        assert exc.context["tier"] == "mid"
        assert exc.context["reason"] == "empty_model"

    def test_incomplete_tier_config_error_context_keys(self) -> None:
        # D-20-17 case (b) partial-set canonical shape.
        exc = IncompleteTierConfigError(
            "partial triplet",
            context={
                "tier": "small",
                "missing_vars": "PERSONA_SMALL_API_KEY",
            },
        )
        assert exc.context["tier"] == "small"
        assert "PERSONA_SMALL_API_KEY" in exc.context["missing_vars"]

    def test_tier_not_configured_error_context_keys(self) -> None:
        # D-20-15 ALL-fail branch canonical shape.
        exc = TierNotConfiguredError(
            "every provider failed to resolve",
            context={
                "tier": "frontier",
                "missing_providers": "nvidia,openai",
                "configured_models": "nvidia/nemotron,openai/gpt-4o",
                "consulted_env_vars": "PERSONA_NVIDIA_API_KEY,PERSONA_OPENAI_API_KEY",
            },
        )
        assert exc.context["tier"] == "frontier"
        assert "nvidia" in exc.context["missing_providers"]
        assert "PERSONA_NVIDIA_API_KEY" in exc.context["consulted_env_vars"]

    @pytest.mark.parametrize("cls", WRAPPER_OR_CONFIG_LAYER)
    def test_wrapper_config_class_accepts_empty_context(self, cls: type[PersonaError]) -> None:
        # ``context`` is optional on every class per the PersonaError base.
        err = cls("smoke message")
        assert "smoke message" in str(err)
        assert err.context == {}

    @pytest.mark.parametrize("cls", [OpenRouterCatalogError, OpenRouterBalanceProbeError])
    def test_openrouter_provider_error_context_shape(self, cls: type[ProviderError]) -> None:
        # Spec 22 D-22-1 / D-22-3 canonical shape: {provider, reason}.
        exc = cls("probe failed", context={"provider": "openrouter", "reason": "timeout"})
        assert exc.context["provider"] == "openrouter"
        assert exc.context["reason"] == "timeout"
        # Provider-layer: caught by ``except ProviderError``.
        assert isinstance(exc, ProviderError)
