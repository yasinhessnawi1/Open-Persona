"""Tests for ``persona.backends.config`` — env-driven BackendConfig + from_env."""

from __future__ import annotations

import pytest
from persona.backends.config import DEFAULT_BASE_URLS, BackendConfig
from pydantic import ValidationError


class TestDefaults:
    def test_default_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Clear any contaminating env vars from the developer's shell.
        for k in (
            "PERSONA_PROVIDER",
            "PERSONA_MODEL",
            "PERSONA_API_KEY",
            "PERSONA_BASE_URL",
            "PERSONA_MAX_TOKENS",
            "PERSONA_TEMPERATURE",
            "PERSONA_REQUEST_TIMEOUT_S",
            "PERSONA_LOCAL_MODEL_ID",
            "PERSONA_LOCAL_QUANTIZATION",
            "PERSONA_LOCAL_DEVICE",
        ):
            monkeypatch.delenv(k, raising=False)
        config = BackendConfig()
        assert config.provider == "anthropic"
        assert config.model == "claude-sonnet-4-6"
        assert config.api_key is None
        assert config.base_url is None
        assert config.max_tokens == 4096
        assert config.temperature == 0.0
        assert config.request_timeout_s == 60.0
        assert config.local_quantization == "4bit"
        assert config.local_device == "auto"


class TestEnvLoading:
    def test_reads_provider_and_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_PROVIDER", "openai")
        monkeypatch.setenv("PERSONA_MODEL", "gpt-4o-mini")
        config = BackendConfig()
        assert config.provider == "openai"
        assert config.model == "gpt-4o-mini"

    def test_reads_api_key_as_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_API_KEY", "sk-secret-123")
        config = BackendConfig()
        assert config.api_key is not None
        assert config.api_key.get_secret_value() == "sk-secret-123"

    def test_api_key_not_in_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_API_KEY", "sk-secret-123")
        config = BackendConfig()
        rendered = repr(config)
        assert "sk-secret-123" not in rendered

    def test_reads_request_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_REQUEST_TIMEOUT_S", "120.0")
        config = BackendConfig()
        assert config.request_timeout_s == 120.0

    def test_reads_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_BASE_URL", "https://my-proxy.example/v1/")
        config = BackendConfig()
        assert config.base_url == "https://my-proxy.example/v1/"


class TestValidation:
    def test_invalid_provider_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_PROVIDER", "bogus")
        with pytest.raises(ValidationError):
            BackendConfig()

    def test_local_provider_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_PROVIDER", "local")
        monkeypatch.setenv("PERSONA_LOCAL_MODEL_ID", "google/gemma-2-9b-it")
        config = BackendConfig()
        assert config.provider == "local"
        assert config.local_model_id == "google/gemma-2-9b-it"

    def test_ollama_provider_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_PROVIDER", "ollama")
        config = BackendConfig()
        assert config.provider == "ollama"

    def test_negative_max_tokens_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_MAX_TOKENS", "-1")
        with pytest.raises(ValidationError):
            BackendConfig()

    def test_negative_timeout_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_REQUEST_TIMEOUT_S", "-1.0")
        with pytest.raises(ValidationError):
            BackendConfig()

    def test_invalid_local_quantization_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_LOCAL_QUANTIZATION", "16bit")
        with pytest.raises(ValidationError):
            BackendConfig()


class TestFromEnv:
    def test_from_env_with_default_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_PROVIDER", "groq")
        config = BackendConfig.from_env()
        assert config.provider == "groq"

    def test_from_env_with_tier_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERSONA_TIER_FRONTIER_PROVIDER", "anthropic")
        monkeypatch.setenv("PERSONA_TIER_FRONTIER_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("PERSONA_TIER_FRONTIER_API_KEY", "sk-frontier")
        # Default-prefix vars should NOT bleed in for the tier load.
        monkeypatch.setenv("PERSONA_PROVIDER", "openai")
        config = BackendConfig.from_env(prefix="PERSONA_TIER_FRONTIER_")
        assert config.provider == "anthropic"
        assert config.model == "claude-opus-4-7"
        assert config.api_key is not None
        assert config.api_key.get_secret_value() == "sk-frontier"

    def test_from_env_falls_back_to_field_defaults_when_no_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Make absolutely sure no env vars match the test prefix.
        prefix = "PERSONA_TEST_EMPTY_"
        for suffix in (
            "PROVIDER",
            "MODEL",
            "API_KEY",
            "BASE_URL",
            "MAX_TOKENS",
            "TEMPERATURE",
            "REQUEST_TIMEOUT_S",
            "LOCAL_MODEL_ID",
            "LOCAL_QUANTIZATION",
            "LOCAL_DEVICE",
        ):
            monkeypatch.delenv(f"{prefix}{suffix}", raising=False)
        config = BackendConfig.from_env(prefix=prefix)
        assert config.provider == "anthropic"
        assert config.model == "claude-sonnet-4-6"


class TestDefaultBaseUrls:
    @pytest.mark.parametrize(
        "provider",
        ["anthropic", "openai", "deepseek", "groq", "together", "ollama"],
    )
    def test_known_provider_has_base_url(self, provider: str) -> None:
        assert provider in DEFAULT_BASE_URLS
        assert DEFAULT_BASE_URLS[provider].startswith(("https://", "http://"))

    def test_local_not_in_base_urls(self) -> None:
        # 'local' is the HF backend; no HTTP endpoint.
        assert "local" not in DEFAULT_BASE_URLS
