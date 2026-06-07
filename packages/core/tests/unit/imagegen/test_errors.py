"""Tests for ``persona.imagegen.errors`` (Spec 15 T02).

Covers the four image-generation exception types, their flat inheritance
under :class:`PersonaError`, and the structured-context formatting they
inherit from :class:`PersonaError`. Mirrors the
``tests/unit/backends/test_backends_errors.py`` shape for the chat
backend errors (Spec 02 mirror discipline, per the Spec 15 kickoff
Dominant Concern #1).
"""

from __future__ import annotations

import pytest
from persona.errors import PersonaError
from persona.imagegen import (
    ContentRejectedError,
    ImageGenError,
    ImageGenUnavailableError,
    ImageProviderError,
)


class TestInheritance:
    def test_image_gen_error_is_persona_error(self) -> None:
        assert issubclass(ImageGenError, PersonaError)

    @pytest.mark.parametrize(
        "subclass",
        [ImageGenUnavailableError, ImageProviderError, ContentRejectedError],
    )
    def test_subclasses_inherit_from_image_gen_error(self, subclass: type[ImageGenError]) -> None:
        assert issubclass(subclass, ImageGenError)
        assert issubclass(subclass, PersonaError)

    def test_subclasses_are_distinct(self) -> None:
        # Symmetric subclassing — none of the three is a parent of another.
        types = (
            ImageGenUnavailableError,
            ImageProviderError,
            ContentRejectedError,
        )
        for a in types:
            for b in types:
                if a is not b:
                    assert not issubclass(a, b), f"{a.__name__} is subclass of {b.__name__}"

    def test_re_exports_match_module(self) -> None:
        # The four exceptions are re-exported via persona.imagegen.__init__.
        from persona.imagegen import errors as errors_module

        assert ImageGenError is errors_module.ImageGenError
        assert ImageGenUnavailableError is errors_module.ImageGenUnavailableError
        assert ImageProviderError is errors_module.ImageProviderError
        assert ContentRejectedError is errors_module.ContentRejectedError


class TestStructuredContext:
    def test_image_gen_error_carries_context(self) -> None:
        err = ImageGenError(
            "upstream failure",
            context={"provider": "openai", "model": "gpt-image-1"},
        )
        rendered = str(err)
        assert "upstream failure" in rendered
        assert "provider=openai" in rendered
        assert "model=gpt-image-1" in rendered

    def test_image_provider_error_records_rate_limit_retry_after(self) -> None:
        err = ImageProviderError(
            "429 from provider",
            context={
                "provider": "openai",
                "reason": "rate_limit",
                "retry_after_s": "30",
            },
        )
        rendered = str(err)
        assert "retry_after_s=30" in rendered
        assert "reason=rate_limit" in rendered

    def test_image_provider_error_records_unsupported_option(self) -> None:
        err = ImageProviderError(
            "size not supported by this model",
            context={
                "provider": "openai",
                "model": "gpt-image-1",
                "reason": "unsupported_option",
                "size": "999x999",
            },
        )
        rendered = str(err)
        assert "reason=unsupported_option" in rendered
        assert "size=999x999" in rendered

    def test_image_gen_unavailable_with_just_provider(self) -> None:
        err = ImageGenUnavailableError(context={"provider": "openai"})
        rendered = str(err)
        assert "provider=openai" in rendered

    def test_content_rejected_provider_input(self) -> None:
        err = ContentRejectedError(
            "provider rejected",
            context={
                "provider": "openai",
                "reason": "provider_moderation",
                "stage": "input",
            },
        )
        rendered = str(err)
        assert "reason=provider_moderation" in rendered
        assert "stage=input" in rendered

    def test_content_rejected_provider_output(self) -> None:
        err = ContentRejectedError(
            "fal flagged the generated image",
            context={
                "provider": "fal",
                "reason": "provider_post_gen_moderation",
                "stage": "output",
            },
        )
        rendered = str(err)
        assert "reason=provider_post_gen_moderation" in rendered
        assert "stage=output" in rendered

    def test_content_rejected_hard_line_carries_only_hash(self) -> None:
        # D-15-X-hard-line-filter: the triggering prompt is NEVER persisted.
        # Only its sha256 hash (prompt_sha256) lives in the context dict.
        err = ContentRejectedError(
            "categorical refusal",
            context={
                "reason": "hard_line_categorical",
                "category": "c1",
                "prompt_sha256": "deadbeef" * 8,
            },
        )
        rendered = str(err)
        assert "reason=hard_line_categorical" in rendered
        assert "category=c1" in rendered
        assert "prompt_sha256=" in rendered

    def test_context_is_optional(self) -> None:
        for cls in (
            ImageGenError,
            ImageGenUnavailableError,
            ImageProviderError,
            ContentRejectedError,
        ):
            err = cls("some message")
            assert "some message" in str(err)

    def test_empty_context_renders_message_only(self) -> None:
        err = ImageGenError("something happened")
        assert str(err) == "something happened"

    def test_context_round_trip_preserves_keys(self) -> None:
        # The base PersonaError stores a copy of the context dict; callers
        # should be able to read keys back off the instance for structured
        # logging.
        ctx = {"provider": "fal", "model": "fal-ai/flux-pro/v1.1"}
        err = ImageProviderError("transient", context=ctx)
        assert err.context["provider"] == "fal"
        assert err.context["model"] == "fal-ai/flux-pro/v1.1"


class TestRaisingAndCatching:
    def test_catch_any_image_gen_error_via_image_gen_error(self) -> None:
        with pytest.raises(ImageGenError):
            raise ImageProviderError(context={"provider": "openai", "reason": "rate_limit"})

    def test_catch_any_image_gen_error_via_persona_error(self) -> None:
        with pytest.raises(PersonaError):
            raise ContentRejectedError(context={"provider": "fal", "reason": "provider_moderation"})

    def test_image_gen_unavailable_does_not_match_provider_error(self) -> None:
        with pytest.raises(ImageGenUnavailableError):
            raise ImageGenUnavailableError(context={"provider": "openai"})
        # ImageProviderError should NOT match an ImageGenUnavailableError clause
        try:
            raise ImageProviderError(context={"provider": "openai", "reason": "transient"})
        except ImageGenUnavailableError:
            pytest.fail("ImageProviderError should not be caught as ImageGenUnavailableError")
        except ImageProviderError:
            pass

    def test_content_rejected_does_not_match_provider_error(self) -> None:
        # Adapters re-raise provider exceptions as either ImageProviderError
        # OR ContentRejectedError; callers branching on the two must not
        # accidentally collapse them via subclassing.
        try:
            raise ContentRejectedError(context={"reason": "provider_moderation", "stage": "input"})
        except ImageProviderError:
            pytest.fail("ContentRejectedError should not be caught as ImageProviderError")
        except ContentRejectedError:
            pass
