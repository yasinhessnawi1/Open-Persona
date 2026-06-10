"""Tests for ``load_backend()`` factory and ``persona.backends`` re-exports."""

# ruff: noqa: ANN401, SLF001

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import patch

import pytest
from persona.backends import (
    BackendConfig,
    ChatBackend,
    OllamaBackend,
    OpenAICompatibleBackend,
    load_backend,
)
from persona.backends.errors import AuthenticationError, ProviderError
from pydantic import SecretStr


def _config(provider: str, **kwargs: Any) -> BackendConfig:
    defaults = {"model": "test-model"}
    defaults.update(kwargs)
    return BackendConfig(provider=provider, **defaults)  # type: ignore[arg-type]


class TestFactory:
    @pytest.mark.parametrize(
        "provider",
        # D-20-X-nvidia-allow-set-extend: nvidia added 2026-06-10 after a
        # production app-startup crash surfaced the factory's allow-set gap
        # (the atomic invariant is actually a FIVE-touch: Provider Literal +
        # DEFAULT_BASE_URLS + _NATIVE_TOOLS_CAPABILITY + _VISION_CAPABILITY +
        # _factory.py's _OPENAI_COMPAT_PROVIDERS).
        ["anthropic", "openai", "deepseek", "groq", "together", "nvidia"],
    )
    def test_openai_compat_providers(self, provider: str) -> None:
        backend = load_backend(_config(provider, api_key=SecretStr("k")))
        assert isinstance(backend, OpenAICompatibleBackend)
        assert isinstance(backend, ChatBackend)
        assert backend.provider_name == provider

    def test_ollama(self) -> None:
        backend = load_backend(_config("ollama"))
        assert isinstance(backend, OllamaBackend)
        assert isinstance(backend, ChatBackend)

    def test_local_with_extras(self) -> None:
        # transformers is installed (we added it via uv); we mock importlib
        # so this test passes regardless of whether the user has the [local]
        # extras when running locally.
        import types

        torch_stub = types.ModuleType("torch")
        transformers_stub = types.ModuleType("transformers")
        original_torch = sys.modules.get("torch")
        original_tf = sys.modules.get("transformers")
        try:
            sys.modules["torch"] = torch_stub
            sys.modules["transformers"] = transformers_stub
            backend = load_backend(_config("local", local_model_id="google/gemma-2-9b-it"))
        finally:
            if original_torch is not None:
                sys.modules["torch"] = original_torch
            else:
                sys.modules.pop("torch", None)
            if original_tf is not None:
                sys.modules["transformers"] = original_tf
            else:
                sys.modules.pop("transformers", None)
        # Imported from inside the factory; checking by class name avoids
        # importing the heavy module at test module level.
        assert type(backend).__name__ == "HFLocalBackend"
        assert backend.provider_name == "local"

    def test_local_without_extras_raises(self) -> None:
        from persona.backends._factory import load_backend as factory

        # Mock importlib.import_module so transformers import raises.
        with patch("importlib.import_module", side_effect=ImportError("no module")):
            with pytest.raises(AuthenticationError) as info:
                factory(_config("local", local_model_id="x"))
            assert "[local] extras" in str(info.value)

    def test_unknown_provider_raises(self) -> None:
        # We can't construct BackendConfig with a bogus provider (Literal),
        # so we test the factory's check by patching past validation.
        from persona.backends._factory import load_backend as factory

        config = _config("anthropic", api_key=SecretStr("k"))
        # Bypass the Literal validation by mutating after construction —
        # this exercises the factory's defensive check.
        object.__setattr__(config, "provider", "bogus")
        with pytest.raises(ProviderError):
            factory(config)


class TestReExports:
    def test_load_backend_exported(self) -> None:
        from persona.backends import load_backend as exported

        assert exported is load_backend

    def test_chat_backend_protocol_exported(self) -> None:
        from persona.backends import ChatBackend as Exported  # noqa: N813

        assert Exported is ChatBackend

    def test_response_types_exported(self) -> None:
        from persona.backends import (
            ChatResponse,
            StreamChunk,
            TokenUsage,
            ToolCallDelta,
            ToolSpec,
        )

        assert all(
            cls is not None
            for cls in [ChatResponse, StreamChunk, TokenUsage, ToolCallDelta, ToolSpec]
        )

    def test_errors_exported(self) -> None:
        from persona.backends import (
            AuthenticationError,
            BackendTimeoutError,
            ModelNotFoundError,
            ProviderError,
            RateLimitError,
        )

        assert all(
            issubclass(cls, ProviderError)
            for cls in [
                AuthenticationError,
                BackendTimeoutError,
                ModelNotFoundError,
                RateLimitError,
            ]
        )
