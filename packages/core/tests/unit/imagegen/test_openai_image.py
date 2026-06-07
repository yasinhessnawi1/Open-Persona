"""Tests for ``persona.imagegen.openai_image`` (Spec 15 T06).

Mirrors ``tests/unit/backends/test_openai_compat.py`` per the Spec 15
decisions gate paragraph #1. The OpenAI SDK is mocked at the adapter
boundary; real provider calls live behind ``@pytest.mark.external`` in
the Spec 15 T20 smoke matrix.

Coverage:

* Capability matrix (``gpt-image-1`` sizes; unlisted-model fallback).
* Size + quality wire mapping (D-15-X-size-rounding +
  ``_OPENAI_QUALITY_MAPPING``).
* Construction-time fail-fast on missing API key
  (:class:`ImageGenUnavailableError`).
* Happy-path response unpack (``b64_json`` decode, ``revised_prompt``
  preservation, dims echoed from wire size, latency_ms positive).
* Error mapping — auth, rate-limit (retry_after_s), not-found, timeout,
  moderation-blocked (input + output stage), generic bad-request,
  connection error, unmapped exception.
* Empty / malformed response defensive checks.
* :class:`ImageBackend` Protocol membership.
"""

# ruff: noqa: ANN401, SLF001 — mocks use Any return types; tests access private attrs

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
from persona.imagegen import (
    ContentRejectedError,
    ImageBackend,
    ImageBackendConfig,
    ImageGenOptions,
    ImageGenUnavailableError,
    ImageProviderError,
)
from persona.imagegen.openai_image import (
    _OPENAI_IMAGE_CAPABILITY,
    _OPENAI_QUALITY_MAPPING,
    _OPENAI_SIZE_ROUNDING,
    OpenAIImageBackend,
    _image_size_supported,
    _is_moderation_blocked,
    _media_type_for_format,
    _moderation_stage,
    _parse_size,
)
from pydantic import SecretStr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(
    *,
    api_key: str | None = "test-key",
    model: str = "gpt-image-1",
    request_timeout_s: float = 60.0,
) -> ImageBackendConfig:
    return ImageBackendConfig(
        provider="openai",
        model=model,
        api_key=SecretStr(api_key) if api_key is not None else None,
        request_timeout_s=request_timeout_s,
    )


def _fake_http_response(*, status: int = 200, headers: dict[str, str] | None = None) -> Any:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.request = MagicMock()
    return resp


def _mock_image_response(
    *,
    n: int = 1,
    b64: str = "aGVsbG8=",  # base64 of "hello"
    revised_prompt: str | None = "the prompt as rewritten by the model",
) -> Any:
    response = MagicMock()
    response.data = [MagicMock(b64_json=b64, revised_prompt=revised_prompt) for _ in range(n)]
    return response


# ---------------------------------------------------------------------------
# Capability matrix + size/quality mapping
# ---------------------------------------------------------------------------


class TestCapabilityMatrix:
    def test_gpt_image_1_supports_three_sizes(self) -> None:
        cap = _OPENAI_IMAGE_CAPABILITY["gpt-image-1"]
        assert isinstance(cap, frozenset)
        assert cap == frozenset({"1024x1024", "1024x1536", "1536x1024"})

    def test_unknown_model_falls_back_to_empty_frozenset(self) -> None:
        assert _image_size_supported("gpt-image-999", "1024x1024") is False

    @pytest.mark.parametrize("size", ["1024x1024", "1024x1536", "1536x1024"])
    def test_supported_sizes_for_gpt_image_1(self, size: str) -> None:
        assert _image_size_supported("gpt-image-1", size) is True

    def test_unsupported_size_for_gpt_image_1(self) -> None:
        # 256x256 is a DALL-E 2 size; not part of gpt-image-1.
        assert _image_size_supported("gpt-image-1", "256x256") is False


class TestSizeRounding:
    def test_square_passes_through(self) -> None:
        assert "1024x1024" not in _OPENAI_SIZE_ROUNDING

    def test_portrait_rounds_to_1024x1536(self) -> None:
        assert _OPENAI_SIZE_ROUNDING["1024x1792"] == "1024x1536"

    def test_landscape_rounds_to_1536x1024(self) -> None:
        assert _OPENAI_SIZE_ROUNDING["1792x1024"] == "1536x1024"


class TestQualityMapping:
    def test_standard_maps_to_medium(self) -> None:
        assert _OPENAI_QUALITY_MAPPING["standard"] == "medium"

    def test_high_passes_through(self) -> None:
        assert _OPENAI_QUALITY_MAPPING["high"] == "high"


class TestParseSize:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            ("1024x1024", (1024, 1024)),
            ("1024x1536", (1024, 1536)),
            ("1536x1024", (1536, 1024)),
            ("1792x1024", (1792, 1024)),
        ],
    )
    def test_round_trip(self, size: str, expected: tuple[int, int]) -> None:
        assert _parse_size(size) == expected


class TestMediaType:
    @pytest.mark.parametrize(
        ("output_format", "media_type"),
        [
            ("png", "image/png"),
            ("jpeg", "image/jpeg"),
            ("webp", "image/webp"),
        ],
    )
    def test_known_formats(self, output_format: str, media_type: str) -> None:
        assert _media_type_for_format(output_format) == media_type

    def test_unknown_format_defaults_to_png(self) -> None:
        assert _media_type_for_format("unknown-format-99") == "image/png"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_construct_with_valid_key(self) -> None:
        backend = OpenAIImageBackend(_config())
        assert backend.provider_name == "openai"
        assert backend.model_name == "gpt-image-1"

    def test_missing_api_key_raises_unavailable(self) -> None:
        with pytest.raises(ImageGenUnavailableError) as info:
            OpenAIImageBackend(_config(api_key=None))
        assert info.value.context["provider"] == "openai"
        assert "missing" in str(info.value).lower()

    def test_empty_api_key_raises_unavailable(self) -> None:
        # SecretStr("") is empty but not None — still fails fast.
        config = ImageBackendConfig(provider="openai", model="gpt-image-1", api_key=SecretStr(""))
        with pytest.raises(ImageGenUnavailableError):
            OpenAIImageBackend(config)

    def test_model_name_echoes_config(self) -> None:
        backend = OpenAIImageBackend(_config(model="gpt-image-1.5"))
        assert backend.model_name == "gpt-image-1.5"

    def test_provider_name_is_constant(self) -> None:
        # Even when wrapping a future variant, provider_name stays
        # ``"openai"`` per the protocol's stable-identifier convention.
        backend = OpenAIImageBackend(_config(model="gpt-image-1.5"))
        assert backend.provider_name == "openai"

    def test_is_image_backend_protocol(self) -> None:
        backend = OpenAIImageBackend(_config())
        assert isinstance(backend, ImageBackend)


# ---------------------------------------------------------------------------
# Generate — happy path + wire-mapping
# ---------------------------------------------------------------------------


class TestGenerateHappyPath:
    @pytest.mark.asyncio
    async def test_generate_returns_generation_result(self) -> None:
        backend = OpenAIImageBackend(_config())
        with patch.object(
            backend._client.images,
            "generate",
            new=AsyncMock(return_value=_mock_image_response()),
        ):
            result = await backend.generate("a red bicycle")
        assert result.provider == "openai"
        assert result.model == "gpt-image-1"
        assert len(result.images) == 1
        assert result.images[0].media_type == "image/png"
        assert result.images[0].image_bytes == b"hello"
        assert result.images[0].workspace_path is None
        assert result.images[0].revised_prompt == "the prompt as rewritten by the model"
        assert result.latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_generate_default_options_passes_square_medium_n1(self) -> None:
        backend = OpenAIImageBackend(_config())
        mock_gen = AsyncMock(return_value=_mock_image_response())
        with patch.object(backend._client.images, "generate", new=mock_gen):
            await backend.generate("a cat")
        kwargs = mock_gen.call_args.kwargs
        assert kwargs["model"] == "gpt-image-1"
        assert kwargs["prompt"] == "a cat"
        assert kwargs["size"] == "1024x1024"
        assert kwargs["quality"] == "medium"
        assert kwargs["n"] == 1

    @pytest.mark.asyncio
    async def test_generate_portrait_rounds_to_1024x1536(self) -> None:
        backend = OpenAIImageBackend(_config())
        options = ImageGenOptions(size="1024x1792", count=1, quality="standard")
        mock_gen = AsyncMock(return_value=_mock_image_response())
        with patch.object(backend._client.images, "generate", new=mock_gen):
            await backend.generate("a cat", options=options)
        assert mock_gen.call_args.kwargs["size"] == "1024x1536"

    @pytest.mark.asyncio
    async def test_generate_landscape_rounds_to_1536x1024(self) -> None:
        backend = OpenAIImageBackend(_config())
        options = ImageGenOptions(size="1792x1024", count=1, quality="standard")
        mock_gen = AsyncMock(return_value=_mock_image_response())
        with patch.object(backend._client.images, "generate", new=mock_gen):
            await backend.generate("a cat", options=options)
        assert mock_gen.call_args.kwargs["size"] == "1536x1024"

    @pytest.mark.asyncio
    async def test_generate_high_quality_passes_through(self) -> None:
        backend = OpenAIImageBackend(_config())
        options = ImageGenOptions(size="1024x1024", count=1, quality="high")
        mock_gen = AsyncMock(return_value=_mock_image_response())
        with patch.object(backend._client.images, "generate", new=mock_gen):
            await backend.generate("a cat", options=options)
        assert mock_gen.call_args.kwargs["quality"] == "high"

    @pytest.mark.asyncio
    async def test_generate_count_two_returns_two_images(self) -> None:
        backend = OpenAIImageBackend(_config())
        options = ImageGenOptions(size="1024x1024", count=2, quality="standard")
        with patch.object(
            backend._client.images,
            "generate",
            new=AsyncMock(return_value=_mock_image_response(n=2)),
        ):
            result = await backend.generate("a cat", options=options)
        assert len(result.images) == 2

    @pytest.mark.asyncio
    async def test_generate_dimensions_match_wire_size(self) -> None:
        # The portrait neutral preset rounds to 1024x1536 on the wire; the
        # returned image dims should reflect the wire size (OpenAI does
        # not echo per-image dims so we derive from wire_size).
        backend = OpenAIImageBackend(_config())
        options = ImageGenOptions(size="1024x1792", count=1, quality="standard")
        with patch.object(
            backend._client.images,
            "generate",
            new=AsyncMock(return_value=_mock_image_response()),
        ):
            result = await backend.generate("a cat", options=options)
        assert result.images[0].width == 1024
        assert result.images[0].height == 1536

    @pytest.mark.asyncio
    async def test_generate_revised_prompt_none_when_absent(self) -> None:
        backend = OpenAIImageBackend(_config())
        with patch.object(
            backend._client.images,
            "generate",
            new=AsyncMock(return_value=_mock_image_response(revised_prompt=None)),
        ):
            result = await backend.generate("a cat")
        assert result.images[0].revised_prompt is None


# ---------------------------------------------------------------------------
# Generate — unsupported (model, size) pair
# ---------------------------------------------------------------------------


class TestUnsupportedOption:
    @pytest.mark.asyncio
    async def test_unsupported_model_raises_unsupported_option(self) -> None:
        # An unknown model with a neutral size that doesn't fall in the
        # closed gpt-image-1 set raises ImageProviderError with reason
        # ``unsupported_option`` BEFORE the SDK is called.
        backend = OpenAIImageBackend(_config(model="gpt-imagined"))
        with (
            patch.object(
                backend._client.images,
                "generate",
                new=AsyncMock(return_value=_mock_image_response()),
            ) as mock_gen,
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "unsupported_option"
        assert info.value.context["model"] == "gpt-imagined"
        # Fails closed — SDK never called.
        assert mock_gen.call_count == 0


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_authentication_error_to_unavailable(self) -> None:
        backend = OpenAIImageBackend(_config())
        exc = openai.AuthenticationError(
            "bad key", response=_fake_http_response(status=401), body=None
        )
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(side_effect=exc)),
            pytest.raises(ImageGenUnavailableError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["provider"] == "openai"

    @pytest.mark.asyncio
    async def test_rate_limit_to_image_provider_error_with_retry_after(self) -> None:
        backend = OpenAIImageBackend(_config())
        exc = openai.RateLimitError(
            "slow down",
            response=_fake_http_response(status=429, headers={"retry-after": "30"}),
            body=None,
        )
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "rate_limit"
        assert info.value.context["retry_after_s"] == "30"

    @pytest.mark.asyncio
    async def test_rate_limit_without_retry_after_header(self) -> None:
        backend = OpenAIImageBackend(_config())
        exc = openai.RateLimitError(
            "slow down", response=_fake_http_response(status=429), body=None
        )
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "rate_limit"
        assert "retry_after_s" not in info.value.context

    @pytest.mark.asyncio
    async def test_not_found_to_model_not_found(self) -> None:
        backend = OpenAIImageBackend(_config(model="gpt-image-imagined"))
        exc = openai.NotFoundError(
            "no such model", response=_fake_http_response(status=404), body=None
        )
        # Use a supported model in capability so we reach the SDK call;
        # patch the lookup at the runtime call.
        with (
            patch(
                "persona.imagegen.openai_image._image_size_supported",
                return_value=True,
            ),
            patch.object(backend._client.images, "generate", new=AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "model_not_found"
        assert info.value.context["model"] == "gpt-image-imagined"

    @pytest.mark.asyncio
    async def test_timeout_to_provider_error(self) -> None:
        backend = OpenAIImageBackend(_config())
        request = MagicMock()
        exc = openai.APITimeoutError(request=request)
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "timeout"

    @pytest.mark.asyncio
    async def test_connection_error_to_provider_error_timeout_reason(self) -> None:
        # APIConnectionError shares the timeout branch (both surface as
        # connection-class failures the caller may retry).
        backend = OpenAIImageBackend(_config())
        request = MagicMock()
        exc = openai.APIConnectionError(request=request)
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "timeout"

    @pytest.mark.asyncio
    async def test_moderation_blocked_input_to_content_rejected(self) -> None:
        backend = OpenAIImageBackend(_config())
        exc = openai.BadRequestError(
            "Your request was rejected by the safety system",
            response=_fake_http_response(status=400),
            body={"error": {"code": "moderation_blocked", "message": "rejected"}},
        )
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(side_effect=exc)),
            pytest.raises(ContentRejectedError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "provider_moderation"
        assert info.value.context["stage"] == "input"

    @pytest.mark.asyncio
    async def test_moderation_blocked_output_to_content_rejected(self) -> None:
        backend = OpenAIImageBackend(_config())
        exc = openai.BadRequestError(
            "The generated image was rejected by safety filters",
            response=_fake_http_response(status=400),
            body={"error": {"code": "moderation_blocked", "message": "output rejected"}},
        )
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(side_effect=exc)),
            pytest.raises(ContentRejectedError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "provider_moderation"
        assert info.value.context["stage"] == "output"

    @pytest.mark.asyncio
    async def test_generic_bad_request_to_provider_error(self) -> None:
        # A 400 without ``moderation_blocked`` is a non-moderation
        # validation error — maps to ImageProviderError, NOT
        # ContentRejectedError.
        backend = OpenAIImageBackend(_config())
        exc = openai.BadRequestError(
            "invalid request",
            response=_fake_http_response(status=400),
            body={"error": {"code": "invalid_request_error", "message": "x"}},
        )
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "bad_request"
        # CRITICAL: not classified as content rejection.
        assert not isinstance(info.value, ContentRejectedError)

    @pytest.mark.asyncio
    async def test_unmapped_error_to_transient(self) -> None:
        backend = OpenAIImageBackend(_config())
        with (
            patch.object(
                backend._client.images,
                "generate",
                new=AsyncMock(side_effect=RuntimeError("weird")),
            ),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"
        assert info.value.context["underlying"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Moderation-detection helpers
# ---------------------------------------------------------------------------


class TestIsModerationBlocked:
    def test_code_attribute_path(self) -> None:
        exc = openai.BadRequestError(
            "rejected",
            response=_fake_http_response(status=400),
            body={"error": {"code": "moderation_blocked", "message": "x"}},
        )
        # The SDK populates exc.code from body.error.code in newer
        # versions; pin the attribute to simulate.
        exc.code = "moderation_blocked"
        assert _is_moderation_blocked(exc) is True

    def test_body_dict_path(self) -> None:
        exc = openai.BadRequestError(
            "rejected",
            response=_fake_http_response(status=400),
            body={"error": {"code": "moderation_blocked", "message": "x"}},
        )
        assert _is_moderation_blocked(exc) is True

    def test_message_substring_fallback(self) -> None:
        exc = openai.BadRequestError(
            "moderation_blocked: content policy",
            response=_fake_http_response(status=400),
            body=None,
        )
        assert _is_moderation_blocked(exc) is True

    def test_non_moderation_returns_false(self) -> None:
        exc = openai.BadRequestError(
            "invalid_request_error: something else",
            response=_fake_http_response(status=400),
            body={"error": {"code": "invalid_request_error"}},
        )
        assert _is_moderation_blocked(exc) is False


class TestModerationStage:
    def test_output_stage_via_generated_image(self) -> None:
        exc = openai.BadRequestError(
            "The generated image was rejected",
            response=_fake_http_response(status=400),
            body=None,
        )
        assert _moderation_stage(exc) == "output"

    def test_output_stage_via_output_keyword(self) -> None:
        exc = openai.BadRequestError(
            "output moderation triggered",
            response=_fake_http_response(status=400),
            body=None,
        )
        assert _moderation_stage(exc) == "output"

    def test_default_stage_is_input(self) -> None:
        exc = openai.BadRequestError(
            "Your prompt was rejected",
            response=_fake_http_response(status=400),
            body=None,
        )
        assert _moderation_stage(exc) == "input"


# ---------------------------------------------------------------------------
# Defensive response shapes
# ---------------------------------------------------------------------------


class TestDefensiveResponseShapes:
    @pytest.mark.asyncio
    async def test_empty_data_array_raises_provider_error(self) -> None:
        backend = OpenAIImageBackend(_config())
        response = MagicMock()
        response.data = []
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(return_value=response)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"
        assert "empty" in str(info.value).lower()

    @pytest.mark.asyncio
    async def test_missing_b64_json_raises_provider_error(self) -> None:
        backend = OpenAIImageBackend(_config())
        response = MagicMock()
        entry = MagicMock(b64_json=None, revised_prompt=None)
        response.data = [entry]
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(return_value=response)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_malformed_base64_raises_provider_error(self) -> None:
        backend = OpenAIImageBackend(_config())
        response = MagicMock()
        entry = MagicMock(b64_json="not-valid-base64!!", revised_prompt=None)
        response.data = [entry]
        with (
            patch.object(backend._client.images, "generate", new=AsyncMock(return_value=response)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"
