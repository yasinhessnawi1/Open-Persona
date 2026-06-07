"""Tests for the spec-13 T04 vision capability surface.

Covers:

* The ``_VISION_CAPABILITY`` matrix entries (D-13-3) and the
  ``_vision_supported`` helper that consults it.
* The :class:`OpenAICompatibleBackend.supports_vision` property per
  ``(provider, model)`` pair.
* The :class:`OllamaBackend.supports_vision` default-off / opt-in shape
  (mirrors D-02-9 for ``use_native_tools``).
* The :class:`HFLocalBackend.supports_vision` default-off shape.
* The new domain exceptions :class:`BackendVisionNotSupportedError` and
  :class:`NoVisionTierConfiguredError` — structured-context shape,
  ``PersonaError`` inheritance, and the locked-flat hierarchy (no
  ``VisionError`` parent, no parent/child relationship between the two)
  per D-13-X-error-hierarchy and D-03-1.

The :class:`HFLocalBackend` construction tests reuse the
``fake_torch``/``fake_transformers``/``patched_imports`` fixtures from
``test_hf_local.py`` via direct re-implementation here so this file
remains self-contained.
"""

# ruff: noqa: ANN401, SLF001, ARG003, ARG002 — fixtures use Any, fake classmethods ignore args

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest
from persona.backends.config import BackendConfig
from persona.backends.errors import (
    BackendVisionNotSupportedError,
    NoVisionTierConfiguredError,
    RoutingConstraintsUnsatisfiableError,
)
from persona.backends.ollama import OllamaBackend
from persona.backends.openai_compat import (
    _VISION_CAPABILITY,
    OpenAICompatibleBackend,
    _vision_supported,
)
from persona.errors import PersonaError
from pydantic import SecretStr

# -----------------------------------------------------------------------------
# _VISION_CAPABILITY matrix (D-13-3)
# -----------------------------------------------------------------------------


class TestVisionCapabilityMatrix:
    def test_anthropic_is_all(self) -> None:
        assert _VISION_CAPABILITY["anthropic"] == "all"

    def test_openai_is_a_frozenset(self) -> None:
        cap = _VISION_CAPABILITY["openai"]
        assert isinstance(cap, frozenset)
        # D-13-3 — the locked entries at spec-13 close-out. Dated variants
        # are verified at deploy (T19) and are not enumerated here.
        assert {"gpt-4o", "gpt-4o-mini", "gpt-4-turbo"} <= cap

    @pytest.mark.parametrize("provider", ["deepseek", "groq", "together"])
    def test_default_off_providers_are_empty_frozensets(self, provider: str) -> None:
        cap = _VISION_CAPABILITY[provider]
        assert cap == frozenset()

    def test_matrix_keys_are_the_five_openai_compatible_providers(self) -> None:
        assert set(_VISION_CAPABILITY) == {
            "anthropic",
            "openai",
            "deepseek",
            "groq",
            "together",
        }


# -----------------------------------------------------------------------------
# _vision_supported helper
# -----------------------------------------------------------------------------


class TestVisionSupportedHelper:
    @pytest.mark.parametrize(
        "model",
        ["claude-3-5-sonnet-20241022", "claude-opus-4-7", "anything-anthropic-ships"],
    )
    def test_anthropic_all_returns_true_for_any_model(self, model: str) -> None:
        assert _vision_supported("anthropic", model) is True

    @pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"])
    def test_openai_known_positive_models(self, model: str) -> None:
        assert _vision_supported("openai", model) is True

    @pytest.mark.parametrize("model", ["gpt-3.5-turbo", "gpt-4", "o1-preview", "text-davinci-003"])
    def test_openai_known_negative_models(self, model: str) -> None:
        assert _vision_supported("openai", model) is False

    @pytest.mark.parametrize("provider", ["deepseek", "groq", "together"])
    def test_default_off_providers_always_false(self, provider: str) -> None:
        assert _vision_supported(provider, "any-model") is False

    def test_unknown_provider_falls_back_to_empty_frozenset(self) -> None:
        # Mirrors `_native_tools_supported` semantics — unknown providers
        # return False rather than raising.
        assert _vision_supported("mystery-provider", "any-model") is False


# -----------------------------------------------------------------------------
# OpenAICompatibleBackend.supports_vision
# -----------------------------------------------------------------------------


def _openai_compat_config(provider: str, model: str, *, api_key: str = "test-key") -> BackendConfig:
    return BackendConfig(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        api_key=SecretStr(api_key),
    )


class TestOpenAICompatibleBackendSupportsVision:
    @pytest.mark.parametrize(
        "model",
        ["claude-3-5-sonnet-20241022", "claude-opus-4-7"],
    )
    def test_anthropic_any_model_supports_vision(self, model: str) -> None:
        backend = OpenAICompatibleBackend(_openai_compat_config("anthropic", model))
        assert backend.supports_vision is True

    @pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"])
    def test_openai_positive_models(self, model: str) -> None:
        backend = OpenAICompatibleBackend(_openai_compat_config("openai", model))
        assert backend.supports_vision is True

    @pytest.mark.parametrize("model", ["gpt-3.5-turbo", "gpt-4", "o1-preview"])
    def test_openai_negative_models(self, model: str) -> None:
        backend = OpenAICompatibleBackend(_openai_compat_config("openai", model))
        assert backend.supports_vision is False

    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            ("deepseek", "deepseek-chat"),
            ("groq", "llama-3.3-70b-versatile"),
            ("together", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
        ],
    )
    def test_text_only_providers_default_to_false(self, provider: str, model: str) -> None:
        backend = OpenAICompatibleBackend(_openai_compat_config(provider, model))
        assert backend.supports_vision is False


# -----------------------------------------------------------------------------
# OllamaBackend.supports_vision
# -----------------------------------------------------------------------------


def _ollama_config(model: str = "llama3") -> BackendConfig:
    return BackendConfig(provider="ollama", model=model)


class TestOllamaBackendSupportsVision:
    def test_default_off(self) -> None:
        backend = OllamaBackend(_ollama_config())
        assert backend.supports_vision is False

    def test_use_vision_true_opts_in(self) -> None:
        backend = OllamaBackend(_ollama_config(), use_vision=True)
        assert backend.supports_vision is True

    def test_use_vision_explicit_false(self) -> None:
        backend = OllamaBackend(_ollama_config(), use_vision=False)
        assert backend.supports_vision is False

    def test_opt_in_is_independent_of_native_tools(self) -> None:
        # The two opt-ins are orthogonal — opting into one does not flip the
        # other.
        backend = OllamaBackend(_ollama_config(), use_native_tools=True, use_vision=False)
        assert backend.supports_native_tools is True
        assert backend.supports_vision is False

        backend2 = OllamaBackend(_ollama_config(), use_native_tools=False, use_vision=True)
        assert backend2.supports_native_tools is False
        assert backend2.supports_vision is True


# -----------------------------------------------------------------------------
# HFLocalBackend.supports_vision
# -----------------------------------------------------------------------------


@pytest.fixture
def _fake_torch() -> Any:
    module = types.ModuleType("torch")
    module.bfloat16 = "bfloat16"  # type: ignore[attr-defined]
    module.float16 = "float16"  # type: ignore[attr-defined]
    module.no_grad = MagicMock(  # type: ignore[attr-defined]
        return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
    )
    return module


@pytest.fixture
def _fake_transformers() -> Any:
    module = types.ModuleType("transformers")

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> FakeTokenizer:
            return cls()

    class FakeModel:
        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> FakeModel:
            return cls()

    module.AutoTokenizer = FakeTokenizer  # type: ignore[attr-defined]
    module.AutoModelForCausalLM = FakeModel  # type: ignore[attr-defined]
    return module


@pytest.fixture
def _patched_imports(_fake_torch: Any, _fake_transformers: Any) -> Any:
    original_modules = sys.modules.copy()
    sys.modules["torch"] = _fake_torch
    sys.modules["transformers"] = _fake_transformers
    yield
    sys.modules.clear()
    sys.modules.update(original_modules)


@pytest.mark.usefixtures("_patched_imports")
class TestHFLocalBackendSupportsVision:
    def test_default_off(self) -> None:
        from persona.backends.hf_local import HFLocalBackend

        config = BackendConfig(
            provider="local",
            model="local-stub",
            local_model_id="google/gemma-2-9b-it",
        )
        backend = HFLocalBackend(config)
        # Matrix-empty at launch (D-13-3) — HF local is text-only at v0.1.
        assert backend.supports_vision is False


# -----------------------------------------------------------------------------
# BackendVisionNotSupportedError
# -----------------------------------------------------------------------------


class TestBackendVisionNotSupportedError:
    def test_is_persona_error(self) -> None:
        assert issubclass(BackendVisionNotSupportedError, PersonaError)

    def test_context_carries_backend_model_and_image_count(self) -> None:
        err = BackendVisionNotSupportedError(
            "vision-only message routed to text-only backend",
            context={
                "backend": "deepseek",
                "model": "deepseek-chat",
                "image_count": "3",
            },
        )
        rendered = str(err)
        assert "backend=deepseek" in rendered
        assert "model=deepseek-chat" in rendered
        assert "image_count=3" in rendered

    def test_raises_and_caught_via_persona_error(self) -> None:
        with pytest.raises(PersonaError):
            raise BackendVisionNotSupportedError(
                context={
                    "backend": "groq",
                    "model": "llama-3.3-70b-versatile",
                    "image_count": "1",
                }
            )

    def test_message_only_no_context(self) -> None:
        err = BackendVisionNotSupportedError("missing-tier")
        assert "missing-tier" in str(err)
        assert err.context == {}


# -----------------------------------------------------------------------------
# NoVisionTierConfiguredError
# -----------------------------------------------------------------------------


class TestNoVisionTierConfiguredError:
    def test_is_persona_error(self) -> None:
        assert issubclass(NoVisionTierConfiguredError, PersonaError)

    def test_context_carries_reason_and_configured_tiers(self) -> None:
        err = NoVisionTierConfiguredError(
            "persona has no vision tier",
            context={
                "reason": "no_vision_tier",
                "configured_tiers": "default,deep",
            },
        )
        rendered = str(err)
        assert "reason=no_vision_tier" in rendered
        assert "configured_tiers=default,deep" in rendered

    def test_raises_and_caught_via_persona_error(self) -> None:
        with pytest.raises(PersonaError):
            raise NoVisionTierConfiguredError(
                context={"reason": "no_vision_tier", "configured_tiers": "default"}
            )


# -----------------------------------------------------------------------------
# Flat hierarchy invariants (D-13-X-error-hierarchy + D-03-1)
# -----------------------------------------------------------------------------


class TestErrorHierarchy:
    """Verify the Spec 13 + Spec 18 error-hierarchy invariants.

    Spec 13 D-13-X-error-hierarchy locked two sibling errors directly under
    ``PersonaError``: :class:`BackendVisionNotSupportedError` (the runtime-side
    capability surface) and :class:`NoVisionTierConfiguredError` (the
    deployment-side configuration surface). No intermediate ``VisionError``;
    no parent/child relationship between the two.

    Spec 18 D-18-X-constraint-failure-shape generalised
    :class:`NoVisionTierConfiguredError` under a new
    :class:`RoutingConstraintsUnsatisfiableError` parent (also a direct child
    of ``PersonaError``) so future capability-constraint filters share one
    parent. ``NoVisionTierConfiguredError`` is preserved as a back-compat
    subclass — existing ``except NoVisionTierConfiguredError`` callers keep
    catching only the Spec 13 case; the new ``RoutingConstraintsUnsatisfiableError``
    is the union surface for future filter classes.

    The invariants below hold across both spec versions:

    * The two original errors are NOT in parent/child relation with each other
      (the Spec 13 D-13-X invariant).
    * Both are reachable from ``PersonaError`` via inheritance (back-compat).
    * The Spec 18 generalisation slots in cleanly: ``NoVisionTierConfigured`` →
      ``RoutingConstraintsUnsatisfiable`` → ``PersonaError``.
    """

    def test_no_vision_tier_is_not_subclass_of_backend_vision_not_supported(
        self,
    ) -> None:
        # D-13-X-error-hierarchy invariant: the two errors are NOT in
        # parent/child relation with each other.
        assert not issubclass(NoVisionTierConfiguredError, BackendVisionNotSupportedError)

    def test_backend_vision_not_supported_is_not_subclass_of_no_vision_tier(
        self,
    ) -> None:
        assert not issubclass(BackendVisionNotSupportedError, NoVisionTierConfiguredError)

    def test_backend_vision_not_supported_is_direct_child_of_persona_error(self) -> None:
        # Spec 13 shape unchanged: the runtime-side capability error is a
        # direct child of PersonaError.
        assert BackendVisionNotSupportedError.__bases__ == (PersonaError,)

    def test_no_vision_tier_is_routing_constraints_unsatisfiable(self) -> None:
        # Spec 18 D-18-X-constraint-failure-shape: NoVisionTierConfiguredError
        # is now a back-compat subclass of RoutingConstraintsUnsatisfiableError.
        # Direct __bases__ check captures the locked hierarchy shape.
        assert NoVisionTierConfiguredError.__bases__ == (RoutingConstraintsUnsatisfiableError,)

    def test_routing_constraints_unsatisfiable_is_direct_child_of_persona_error(
        self,
    ) -> None:
        # The Spec 18 generalisation parent sits directly under PersonaError —
        # one intermediate level between NoVisionTierConfiguredError and
        # PersonaError, by design.
        assert RoutingConstraintsUnsatisfiableError.__bases__ == (PersonaError,)

    def test_no_vision_tier_remains_reachable_from_persona_error(self) -> None:
        # Back-compat invariant: ``except PersonaError`` still catches
        # NoVisionTierConfiguredError via the inheritance chain.
        assert issubclass(NoVisionTierConfiguredError, PersonaError)
        # And the Spec 13 ``except NoVisionTierConfiguredError`` callers
        # still catch only the Spec 13 case (not RoutingConstraintsUnsatisfiable
        # at large).
        assert not issubclass(RoutingConstraintsUnsatisfiableError, NoVisionTierConfiguredError)
