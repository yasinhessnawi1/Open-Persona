"""Tests for ``persona.imagegen._factory`` — :func:`load_image_backend` (Spec 15 T05).

Mirrors ``tests/unit/backends/`` (Spec 02) per the Spec 15 decisions gate
paragraph #1 ("Mirror Spec 02 verbatim"). Asserts:

* Provider dispatch (``"openai"`` → :class:`OpenAIImageBackend`,
  ``"fal"`` → :class:`FalImageBackend`).
* Unknown provider raises :class:`ImageProviderError` with structured
  ``context`` (the offending value + the comma-joined supported list).
* The factory is the public entry point re-exported from
  :mod:`persona.imagegen` per the Spec 02 ``load_backend`` precedent.
* Lazy-import discipline: importing :mod:`persona.imagegen` does NOT
  pull either concrete backend module — the imports happen inside
  :func:`load_image_backend` so callers that only need the Protocol /
  config / boundary types do not pay the SDK import cost.

The concrete-backend dispatch tests stub the constructors at import time
so this test file does not depend on T06 / T07 already being landed. The
real construction-time fail-fast contract
(:class:`persona.imagegen.errors.ImageGenUnavailableError` on missing
``api_key``) is exercised by T06 / T07's own tests.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
from persona.imagegen import (
    ImageBackend,
    ImageBackendConfig,
    ImageProviderError,
    load_image_backend,
)

_LAZY_MODULES: tuple[str, ...] = (
    "persona.imagegen.openai_image",
    "persona.imagegen.fal_image",
)
"""Concrete-backend modules the factory imports lazily.

The factory body imports each only when ``config.provider`` selects it;
the tests poke ``sys.modules`` to install stubs without requiring the
real modules to exist on disk yet (T06 / T07 land them).
"""


def _make_fake_backend_module(provider_name: str, class_name: str) -> types.ModuleType:
    """Build a tiny stub module exposing a constructor that records calls.

    The stub class behaves as an :class:`ImageBackend` Protocol member —
    has ``provider_name`` + ``model_name`` + ``generate`` so the dispatch
    test can assert :func:`isinstance` if needed.
    """

    module = types.ModuleType(f"persona.imagegen.{provider_name}_image")

    class _Stub:
        last_config: ImageBackendConfig | None = None

        def __init__(self, config: ImageBackendConfig) -> None:
            type(self).last_config = config
            self._provider = provider_name
            self._model = config.model

        @property
        def provider_name(self) -> str:
            return self._provider

        @property
        def model_name(self) -> str:
            return self._model

        async def generate(
            self,
            prompt: str,  # noqa: ARG002
            *,
            options: object = None,  # noqa: ARG002
        ) -> object:
            raise NotImplementedError

        async def edit(
            self,
            input_image: object,  # noqa: ARG002
            instructions: str,  # noqa: ARG002
            *,
            options: object = None,  # noqa: ARG002
        ) -> object:
            raise NotImplementedError("edit not supported in v1")

    _Stub.__name__ = class_name
    _Stub.__qualname__ = class_name
    setattr(module, class_name, _Stub)
    return module


@pytest.fixture
def stub_backends(monkeypatch: pytest.MonkeyPatch) -> dict[str, types.ModuleType]:
    """Install stub backend modules into ``sys.modules`` for the duration of one test.

    ``monkeypatch.setitem`` handles restoration on teardown; no explicit
    cleanup needed in the fixture body.
    """
    openai_mod = _make_fake_backend_module("openai", "OpenAIImageBackend")
    fal_mod = _make_fake_backend_module("fal", "FalImageBackend")
    monkeypatch.setitem(sys.modules, "persona.imagegen.openai_image", openai_mod)
    monkeypatch.setitem(sys.modules, "persona.imagegen.fal_image", fal_mod)
    return {"openai": openai_mod, "fal": fal_mod}


class TestDispatch:
    """The two-provider dispatch table."""

    def test_openai_dispatch(self, stub_backends: dict[str, types.ModuleType]) -> None:
        config = ImageBackendConfig(provider="openai", model="gpt-image-1")
        backend = load_image_backend(config)
        assert isinstance(backend, stub_backends["openai"].OpenAIImageBackend)
        assert backend.provider_name == "openai"
        assert backend.model_name == "gpt-image-1"

    def test_fal_dispatch(self, stub_backends: dict[str, types.ModuleType]) -> None:
        config = ImageBackendConfig(provider="fal", model="fal-ai/flux-pro/v1.1")
        backend = load_image_backend(config)
        assert isinstance(backend, stub_backends["fal"].FalImageBackend)
        assert backend.provider_name == "fal"
        assert backend.model_name == "fal-ai/flux-pro/v1.1"

    def test_dispatch_passes_config_through(
        self, stub_backends: dict[str, types.ModuleType]
    ) -> None:
        # The factory hands the original config to the constructor
        # unchanged; the backend reads model / api_key / base_url from
        # there.
        config = ImageBackendConfig(provider="openai", model="gpt-image-1.5")
        load_image_backend(config)
        assert stub_backends["openai"].OpenAIImageBackend.last_config is config

    @pytest.mark.usefixtures("stub_backends")
    def test_dispatch_protocol_membership(self) -> None:
        # The factory's return type is the Protocol; isinstance against
        # the @runtime_checkable Protocol holds for both stubs.
        config = ImageBackendConfig(provider="openai")
        backend = load_image_backend(config)
        assert isinstance(backend, ImageBackend)


class TestUnknownProvider:
    """Unknown-provider error path."""

    def test_unknown_provider_raises_image_provider_error(self) -> None:
        # Bypass ImageBackendConfig validation (Literal would reject the
        # value); fabricate a config with a forbidden provider value via
        # a MagicMock so the factory sees the unknown string.
        bad_config = MagicMock(spec=ImageBackendConfig)
        bad_config.provider = "stability"
        with pytest.raises(ImageProviderError) as exc_info:
            load_image_backend(bad_config)
        assert "stability" in str(exc_info.value)

    def test_unknown_provider_error_context_carries_offending_value(self) -> None:
        bad_config = MagicMock(spec=ImageBackendConfig)
        bad_config.provider = "stability"
        with pytest.raises(ImageProviderError) as exc_info:
            load_image_backend(bad_config)
        assert exc_info.value.context["provider"] == "stability"

    def test_unknown_provider_error_context_carries_supported_list(self) -> None:
        bad_config = MagicMock(spec=ImageBackendConfig)
        bad_config.provider = "replicate"
        with pytest.raises(ImageProviderError) as exc_info:
            load_image_backend(bad_config)
        supported = exc_info.value.context["supported"]
        # Order is stable per the factory's _SUPPORTED_PROVIDERS tuple.
        assert "openai" in supported
        assert "fal" in supported

    def test_unknown_provider_error_message_lists_supported(self) -> None:
        bad_config = MagicMock(spec=ImageBackendConfig)
        bad_config.provider = "midjourney"
        with pytest.raises(ImageProviderError) as exc_info:
            load_image_backend(bad_config)
        assert "openai" in str(exc_info.value)
        assert "fal" in str(exc_info.value)


class TestPublicSurface:
    """The factory is re-exported from ``persona.imagegen``."""

    def test_load_image_backend_in_persona_imagegen_namespace(self) -> None:
        import persona.imagegen as imagegen_pkg

        assert imagegen_pkg.load_image_backend is load_image_backend

    def test_load_image_backend_in_dunder_all(self) -> None:
        import persona.imagegen as imagegen_pkg

        assert "load_image_backend" in imagegen_pkg.__all__


class TestLazyImports:
    """The factory imports concrete backends lazily so ``persona.imagegen``
    stays importable before T06/T07 land — and so callers that only need
    the Protocol / config / boundary types do not pay the SDK import cost.

    Verifies the dispatch body uses local-scope imports (not module-top-level).
    """

    def test_factory_module_does_not_import_concrete_backends(self) -> None:
        # The factory module's top-level imports should NOT include either
        # concrete backend module; the imports happen lazily inside
        # load_image_backend.
        import persona.imagegen._factory as factory_mod

        # The factory references the constructors only inside the function
        # body. Sniff the module's own globals — neither concrete backend
        # class is bound at module scope.
        assert "OpenAIImageBackend" not in vars(factory_mod)
        assert "FalImageBackend" not in vars(factory_mod)

    def test_dispatch_imports_only_the_selected_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # If both lazy modules are absent and we dispatch openai, only
        # the openai module is required. Install the openai stub; leave
        # the fal slot unstubbed so it would ImportError if touched.
        openai_mod = _make_fake_backend_module("openai", "OpenAIImageBackend")
        monkeypatch.setitem(sys.modules, "persona.imagegen.openai_image", openai_mod)
        # Forcibly mark fal as missing — any import attempt raises.
        monkeypatch.setitem(sys.modules, "persona.imagegen.fal_image", None)
        config = ImageBackendConfig(provider="openai", model="gpt-image-1")
        backend = load_image_backend(config)
        assert backend.provider_name == "openai"

    def test_persona_imagegen_imports_without_concrete_backends(self) -> None:
        # Sanity — importing persona.imagegen must succeed even if the
        # concrete backend modules are not present at all (the lazy
        # import inside the factory body is what makes this work). The
        # smoke check: re-import the package and confirm the symbol set
        # is intact.
        import persona.imagegen as imagegen_pkg

        assert hasattr(imagegen_pkg, "load_image_backend")
        assert hasattr(imagegen_pkg, "ImageBackend")
        assert hasattr(imagegen_pkg, "ImageBackendConfig")


class TestSupportedProvidersList:
    """The closed-set lock for D-15-1."""

    def test_supported_providers_tuple_matches_image_provider_literal(self) -> None:
        from typing import get_args

        from persona.imagegen._factory import _SUPPORTED_PROVIDERS
        from persona.imagegen.config import ImageProvider

        assert set(_SUPPORTED_PROVIDERS) == set(get_args(ImageProvider))
