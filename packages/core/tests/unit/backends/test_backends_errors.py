"""Tests for ``persona.backends.errors``.

Covers the five backend exception types, their inheritance, and the
structured-context formatting inherited from :class:`PersonaError`.
"""

from __future__ import annotations

import pytest
from persona.backends.errors import (
    AuthenticationError,
    BackendTimeoutError,
    ModelNotFoundError,
    ProviderError,
    RateLimitError,
)
from persona.errors import PersonaError


class TestInheritance:
    def test_provider_error_is_persona_error(self) -> None:
        assert issubclass(ProviderError, PersonaError)

    @pytest.mark.parametrize(
        "subclass",
        [AuthenticationError, RateLimitError, ModelNotFoundError, BackendTimeoutError],
    )
    def test_subclasses_inherit_from_provider_error(self, subclass: type[ProviderError]) -> None:
        assert issubclass(subclass, ProviderError)
        assert issubclass(subclass, PersonaError)

    def test_subclasses_are_distinct(self) -> None:
        # Symmetric subclassing — none of the four is a parent of another.
        types = (
            AuthenticationError,
            RateLimitError,
            ModelNotFoundError,
            BackendTimeoutError,
        )
        for a in types:
            for b in types:
                if a is not b:
                    assert not issubclass(a, b), f"{a.__name__} is subclass of {b.__name__}"


class TestStructuredContext:
    def test_provider_error_carries_context(self) -> None:
        err = ProviderError("upstream failure", context={"provider": "anthropic", "model": "x"})
        rendered = str(err)
        assert "upstream failure" in rendered
        assert "provider=anthropic" in rendered
        assert "model=x" in rendered

    def test_rate_limit_records_retry_after(self) -> None:
        err = RateLimitError(
            "429 from provider",
            context={"provider": "anthropic", "retry_after_s": "30"},
        )
        rendered = str(err)
        assert "retry_after_s=30" in rendered
        assert "provider=anthropic" in rendered

    def test_authentication_error_with_just_provider(self) -> None:
        err = AuthenticationError(context={"provider": "openai"})
        rendered = str(err)
        assert "provider=openai" in rendered

    def test_model_not_found_includes_model(self) -> None:
        err = ModelNotFoundError(
            "model unknown",
            context={"provider": "groq", "model": "imaginary-model-9000"},
        )
        rendered = str(err)
        assert "model=imaginary-model-9000" in rendered

    def test_timeout_error_with_empty_context_is_fine(self) -> None:
        err = BackendTimeoutError("request exceeded 60s")
        assert "request exceeded 60s" in str(err)

    def test_context_is_optional(self) -> None:
        for cls in (
            ProviderError,
            AuthenticationError,
            RateLimitError,
            ModelNotFoundError,
            BackendTimeoutError,
        ):
            err = cls("some message")
            assert "some message" in str(err)


class TestRaisingAndCatching:
    def test_catch_any_backend_error_via_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            raise RateLimitError(context={"provider": "anthropic"})

    def test_catch_any_backend_error_via_persona_error(self) -> None:
        with pytest.raises(PersonaError):
            raise BackendTimeoutError(context={"provider": "ollama"})

    def test_authentication_does_not_match_rate_limit(self) -> None:
        with pytest.raises(AuthenticationError):
            raise AuthenticationError(context={"provider": "openai"})
        # RateLimitError should NOT match an AuthenticationError except clause
        try:
            raise RateLimitError(context={"provider": "openai"})
        except AuthenticationError:
            pytest.fail("RateLimitError should not be caught as AuthenticationError")
        except RateLimitError:
            pass
