"""Tests for ``persona.imagegen.openrouter_image`` (Spec 22 T09a).

Mirrors ``tests/unit/imagegen/test_openai_image.py`` per the Spec 15
decisions gate paragraph #1. OpenRouter has no ``/images/generations``
route — image generation rides ``chat.completions.create`` and the image
comes back as a base64 data URL inside the assistant message's untyped
``images`` extra (R-22-3). The openai SDK is mocked at the adapter
boundary; real provider calls live behind ``@pytest.mark.external``.

Coverage:

* ``image_config`` coercion from neutral ``ImageGenOptions.size``
  (D-22-19).
* Construction-time fail-fast on missing API key
  (:class:`ImageGenUnavailableError`).
* Happy-path response unpack (data-URL decode, media-type parse, dims
  echo the neutral requested size, text residue discarded).
* ``count > 1`` raises ``ImageProviderError(reason="unsupported_option")``.
* Error mapping — auth (D-22-9), rate-limit (retry_after_s), moderation
  (D-22-16 input + output stage).
* Defensive response shapes — empty images array, missing / malformed
  data URL.
* :class:`ImageBackend` Protocol membership.
"""

# ruff: noqa: ANN401, SLF001 — mocks use Any return types; tests access private attrs

from __future__ import annotations

import base64
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
from persona.imagegen.openrouter_image import (
    _OPENROUTER_IMAGE_BASE_URL,
    _SIZE_TO_IMAGE_CONFIG,
    OpenRouterImageBackend,
    _media_type_for,
    _parse_size,
)
from pydantic import SecretStr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(
    *,
    api_key: str | None = "test-key",
    model: str = "google/gemini-2.5-flash-image-preview",
    request_timeout_s: float = 60.0,
) -> ImageBackendConfig:
    return ImageBackendConfig(
        provider="openai",  # T09b adds "openrouter" to the Literal; not asserted here.
        model=model,
        api_key=SecretStr(api_key) if api_key is not None else None,
        request_timeout_s=request_timeout_s,
    )


_HELLO_PNG_B64 = base64.b64encode(b"hello").decode("ascii")  # "aGVsbG8="


def _data_url(*, media_type: str = "image/png", b64: str = _HELLO_PNG_B64) -> str:
    return f"data:{media_type};base64,{b64}"


def _mock_chat_response(
    *,
    url: str | None = None,
    content: str | None = "here is your image",
    images: Any = "__default__",
    via_attribute: bool = False,
) -> Any:
    """Build a fake chat-completion whose message carries the ``images`` extra.

    ``images`` defaults to a single ``image_url`` entry pointing at a
    base64 data URL. Pass ``images=[]`` for the empty-array case or
    ``images=None`` to omit it entirely. ``via_attribute`` surfaces the
    extra as a plain ``.images`` attribute instead of ``model_extra``.
    """
    if images == "__default__":
        the_url = url if url is not None else _data_url()
        images = [{"type": "image_url", "image_url": {"url": the_url}}]

    message = MagicMock()
    message.content = content
    if via_attribute:
        message.model_extra = None
        message.images = images
    else:
        message.model_extra = {"images": images}
        message.images = images  # mirrors SDK; model_extra is preferred path

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


def _fake_http_response(*, status: int = 200, headers: dict[str, str] | None = None) -> Any:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.request = MagicMock()
    return resp


def _patch_create(backend: OpenRouterImageBackend, mock: AsyncMock) -> Any:
    return patch.object(backend._client.chat.completions, "create", new=mock)


# ---------------------------------------------------------------------------
# Coercion tables + helpers
# ---------------------------------------------------------------------------


class TestSizeToImageConfig:
    def test_square_maps_to_1k_1to1(self) -> None:
        assert _SIZE_TO_IMAGE_CONFIG["1024x1024"] == {
            "aspect_ratio": "1:1",
            "image_size": "1K",
        }

    def test_portrait_maps_to_2k_9to16(self) -> None:
        assert _SIZE_TO_IMAGE_CONFIG["1024x1792"] == {
            "aspect_ratio": "9:16",
            "image_size": "2K",
        }

    def test_landscape_maps_to_2k_16to9(self) -> None:
        assert _SIZE_TO_IMAGE_CONFIG["1792x1024"] == {
            "aspect_ratio": "16:9",
            "image_size": "2K",
        }


class TestParseSize:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            ("1024x1024", (1024, 1024)),
            ("1024x1792", (1024, 1792)),
            ("1792x1024", (1792, 1024)),
        ],
    )
    def test_round_trip(self, size: str, expected: tuple[int, int]) -> None:
        assert _parse_size(size) == expected


class TestMediaType:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("image/png", "image/png"),
            ("image/jpeg", "image/jpeg"),
            ("image/webp", "image/webp"),
        ],
    )
    def test_known_formats(self, raw: str, expected: str) -> None:
        assert _media_type_for(raw) == expected

    def test_unknown_format_defaults_to_png(self) -> None:
        assert _media_type_for("image/heic") == "image/png"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_construct_with_valid_key(self) -> None:
        backend = OpenRouterImageBackend(_config())
        assert backend.provider_name == "openrouter"
        assert backend.model_name == "google/gemini-2.5-flash-image-preview"

    def test_default_base_url(self) -> None:
        backend = OpenRouterImageBackend(_config())
        assert backend._base_url == _OPENROUTER_IMAGE_BASE_URL
        assert _OPENROUTER_IMAGE_BASE_URL == "https://openrouter.ai/api/v1/"

    def test_base_url_override(self) -> None:
        config = ImageBackendConfig(
            provider="openai",
            model="google/gemini-2.5-flash-image-preview",
            api_key=SecretStr("k"),
            base_url="https://proxy.local/v1/",
        )
        backend = OpenRouterImageBackend(config)
        assert backend._base_url == "https://proxy.local/v1/"

    def test_missing_api_key_raises_unavailable(self) -> None:
        with pytest.raises(ImageGenUnavailableError) as info:
            OpenRouterImageBackend(_config(api_key=None))
        assert info.value.context["provider"] == "openrouter"
        assert "missing" in str(info.value).lower()

    def test_empty_api_key_raises_unavailable(self) -> None:
        config = ImageBackendConfig(
            provider="openai",
            model="google/gemini-2.5-flash-image-preview",
            api_key=SecretStr(""),
        )
        with pytest.raises(ImageGenUnavailableError):
            OpenRouterImageBackend(config)

    def test_model_name_echoes_config(self) -> None:
        backend = OpenRouterImageBackend(_config(model="black-forest-labs/flux-1.1-pro"))
        assert backend.model_name == "black-forest-labs/flux-1.1-pro"

    def test_is_image_backend_protocol(self) -> None:
        backend = OpenRouterImageBackend(_config())
        assert isinstance(backend, ImageBackend)


# ---------------------------------------------------------------------------
# Generate — happy path + wire-mapping
# ---------------------------------------------------------------------------


class TestGenerateHappyPath:
    @pytest.mark.asyncio
    async def test_generate_returns_generation_result(self) -> None:
        backend = OpenRouterImageBackend(_config())
        mock = AsyncMock(return_value=_mock_chat_response())
        with _patch_create(backend, mock):
            result = await backend.generate("a red bicycle")
        assert result.provider == "openrouter"
        assert result.model == "google/gemini-2.5-flash-image-preview"
        assert len(result.images) == 1
        image = result.images[0]
        assert image.media_type == "image/png"
        assert image.image_bytes == b"hello"
        assert image.workspace_path is None
        assert image.width == 1024
        assert image.height == 1024
        assert result.latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_generate_sends_modalities_and_image_config(self) -> None:
        backend = OpenRouterImageBackend(_config())
        mock = AsyncMock(return_value=_mock_chat_response())
        with _patch_create(backend, mock):
            await backend.generate("a cat")
        kwargs = mock.call_args.kwargs
        assert kwargs["model"] == "google/gemini-2.5-flash-image-preview"
        assert kwargs["messages"] == [{"role": "user", "content": "a cat"}]
        extra = kwargs["extra_body"]
        assert extra["modalities"] == ["image", "text"]
        assert extra["image_config"] == {"aspect_ratio": "1:1", "image_size": "1K"}

    @pytest.mark.asyncio
    async def test_generate_portrait_image_config_and_dims(self) -> None:
        backend = OpenRouterImageBackend(_config())
        options = ImageGenOptions(size="1024x1792", count=1, quality="standard")
        mock = AsyncMock(return_value=_mock_chat_response())
        with _patch_create(backend, mock):
            result = await backend.generate("a cat", options=options)
        extra = mock.call_args.kwargs["extra_body"]
        assert extra["image_config"] == {"aspect_ratio": "9:16", "image_size": "2K"}
        assert result.images[0].width == 1024
        assert result.images[0].height == 1792

    @pytest.mark.asyncio
    async def test_generate_landscape_image_config_and_dims(self) -> None:
        backend = OpenRouterImageBackend(_config())
        options = ImageGenOptions(size="1792x1024", count=1, quality="high")
        mock = AsyncMock(return_value=_mock_chat_response())
        with _patch_create(backend, mock):
            result = await backend.generate("a cat", options=options)
        extra = mock.call_args.kwargs["extra_body"]
        assert extra["image_config"] == {"aspect_ratio": "16:9", "image_size": "2K"}
        assert result.images[0].width == 1792
        assert result.images[0].height == 1024

    @pytest.mark.asyncio
    async def test_generate_decodes_jpeg_media_type(self) -> None:
        backend = OpenRouterImageBackend(_config())
        url = _data_url(media_type="image/jpeg")
        mock = AsyncMock(return_value=_mock_chat_response(url=url))
        with _patch_create(backend, mock):
            result = await backend.generate("a cat")
        assert result.images[0].media_type == "image/jpeg"
        assert result.images[0].image_bytes == b"hello"

    @pytest.mark.asyncio
    async def test_generate_text_residue_discarded(self) -> None:
        # Gemini-class models append text alongside the image; the result
        # must stay image-centric (no text field on GenerationResult).
        backend = OpenRouterImageBackend(_config())
        mock = AsyncMock(return_value=_mock_chat_response(content="A lovely cat for you!"))
        with _patch_create(backend, mock):
            result = await backend.generate("a cat")
        assert len(result.images) == 1
        assert result.images[0].image_bytes == b"hello"

    @pytest.mark.asyncio
    async def test_generate_reads_images_via_attribute_fallback(self) -> None:
        # When the SDK surfaces ``images`` as a plain attribute (model_extra
        # is None) the getattr fallback path must still find it.
        backend = OpenRouterImageBackend(_config())
        mock = AsyncMock(return_value=_mock_chat_response(via_attribute=True))
        with _patch_create(backend, mock):
            result = await backend.generate("a cat")
        assert result.images[0].image_bytes == b"hello"

    @pytest.mark.asyncio
    async def test_generate_handles_object_shaped_image_url(self) -> None:
        # The SDK may parse the nested image_url as an object, not a dict.
        backend = OpenRouterImageBackend(_config())
        image_url = MagicMock()
        image_url.url = _data_url()
        entry = MagicMock()
        entry.image_url = image_url
        mock = AsyncMock(return_value=_mock_chat_response(images=[entry]))
        with _patch_create(backend, mock):
            result = await backend.generate("a cat")
        assert result.images[0].image_bytes == b"hello"


# ---------------------------------------------------------------------------
# Generate — unsupported count
# ---------------------------------------------------------------------------


class TestUnsupportedOption:
    @pytest.mark.asyncio
    async def test_count_two_raises_unsupported_option(self) -> None:
        backend = OpenRouterImageBackend(_config())
        options = ImageGenOptions(size="1024x1024", count=2, quality="standard")
        mock = AsyncMock(return_value=_mock_chat_response())
        with _patch_create(backend, mock), pytest.raises(ImageProviderError) as info:
            await backend.generate("a cat", options=options)
        assert info.value.context["reason"] == "unsupported_option"
        assert info.value.context["count"] == "2"
        # Fails closed — SDK never called.
        assert mock.call_count == 0


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_authentication_error_to_unavailable(self) -> None:
        backend = OpenRouterImageBackend(_config())
        exc = openai.AuthenticationError(
            "bad key", response=_fake_http_response(status=401), body=None
        )
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ImageGenUnavailableError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["provider"] == "openrouter"

    @pytest.mark.asyncio
    async def test_rate_limit_with_retry_after(self) -> None:
        backend = OpenRouterImageBackend(_config())
        exc = openai.RateLimitError(
            "slow down",
            response=_fake_http_response(status=429, headers={"retry-after": "30"}),
            body=None,
        )
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "rate_limit"
        assert info.value.context["retry_after_s"] == "30"

    @pytest.mark.asyncio
    async def test_rate_limit_without_retry_after(self) -> None:
        backend = OpenRouterImageBackend(_config())
        exc = openai.RateLimitError(
            "slow down", response=_fake_http_response(status=429), body=None
        )
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "rate_limit"
        assert "retry_after_s" not in info.value.context

    @pytest.mark.asyncio
    async def test_not_found_to_model_not_found(self) -> None:
        backend = OpenRouterImageBackend(_config(model="no/such-model"))
        exc = openai.NotFoundError(
            "no such model", response=_fake_http_response(status=404), body=None
        )
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "model_not_found"
        assert info.value.context["model"] == "no/such-model"

    @pytest.mark.asyncio
    async def test_timeout_to_provider_error(self) -> None:
        backend = OpenRouterImageBackend(_config())
        exc = openai.APITimeoutError(request=MagicMock())
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "timeout"

    @pytest.mark.asyncio
    async def test_moderation_bad_request_input_to_content_rejected(self) -> None:
        backend = OpenRouterImageBackend(_config())
        exc = openai.BadRequestError(
            "Your prompt was flagged by moderation",
            response=_fake_http_response(status=400),
            body={"error": {"code": "moderation", "message": "rejected"}},
        )
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ContentRejectedError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "provider_moderation"
        assert info.value.context["stage"] == "input"

    @pytest.mark.asyncio
    async def test_moderation_bad_request_output_to_content_rejected(self) -> None:
        backend = OpenRouterImageBackend(_config())
        exc = openai.BadRequestError(
            "The generated image was flagged by moderation",
            response=_fake_http_response(status=400),
            body={"error": {"code": "moderation", "message": "rejected"}},
        )
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ContentRejectedError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "provider_moderation"
        assert info.value.context["stage"] == "output"

    @pytest.mark.asyncio
    async def test_moderation_permission_denied_to_content_rejected(self) -> None:
        # A 403 moderation rejection must surface as ContentRejectedError,
        # never ImageProviderError (Spec 15 SURFACE invariant, D-22-16).
        backend = OpenRouterImageBackend(_config())
        exc = openai.PermissionDeniedError(
            "request flagged by moderation policy",
            response=_fake_http_response(status=403),
            body={"error": {"code": "flagged"}},
        )
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ContentRejectedError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "provider_moderation"

    @pytest.mark.asyncio
    async def test_non_moderation_permission_denied_to_unavailable(self) -> None:
        backend = OpenRouterImageBackend(_config())
        exc = openai.PermissionDeniedError(
            "your key cannot access this resource",
            response=_fake_http_response(status=403),
            body={"error": {"code": "insufficient_permissions"}},
        )
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ImageGenUnavailableError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["provider"] == "openrouter"

    @pytest.mark.asyncio
    async def test_generic_bad_request_to_provider_error(self) -> None:
        backend = OpenRouterImageBackend(_config())
        exc = openai.BadRequestError(
            "invalid request",
            response=_fake_http_response(status=400),
            body={"error": {"code": "invalid_request_error", "message": "x"}},
        )
        with (
            _patch_create(backend, AsyncMock(side_effect=exc)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "bad_request"
        assert not isinstance(info.value, ContentRejectedError)

    @pytest.mark.asyncio
    async def test_unmapped_error_to_transient(self) -> None:
        backend = OpenRouterImageBackend(_config())
        with (
            _patch_create(backend, AsyncMock(side_effect=RuntimeError("weird"))),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"
        assert info.value.context["underlying"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Defensive response shapes
# ---------------------------------------------------------------------------


class TestDefensiveResponseShapes:
    @pytest.mark.asyncio
    async def test_no_choices_raises_transient(self) -> None:
        backend = OpenRouterImageBackend(_config())
        response = MagicMock()
        response.choices = []
        with (
            _patch_create(backend, AsyncMock(return_value=response)),
            pytest.raises(ImageProviderError) as info,
        ):
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_empty_images_array_raises_transient(self) -> None:
        backend = OpenRouterImageBackend(_config())
        mock = AsyncMock(return_value=_mock_chat_response(images=[]))
        with _patch_create(backend, mock), pytest.raises(ImageProviderError) as info:
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_missing_images_extra_raises_transient(self) -> None:
        backend = OpenRouterImageBackend(_config())
        mock = AsyncMock(return_value=_mock_chat_response(images=None, via_attribute=True))
        with _patch_create(backend, mock), pytest.raises(ImageProviderError) as info:
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_non_data_url_raises_transient(self) -> None:
        backend = OpenRouterImageBackend(_config())
        mock = AsyncMock(return_value=_mock_chat_response(url="https://cdn.example/img.png"))
        with _patch_create(backend, mock), pytest.raises(ImageProviderError) as info:
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_malformed_base64_raises_transient(self) -> None:
        backend = OpenRouterImageBackend(_config())
        bad_url = "data:image/png;base64,not-valid-base64!!"
        mock = AsyncMock(return_value=_mock_chat_response(url=bad_url))
        with _patch_create(backend, mock), pytest.raises(ImageProviderError) as info:
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_missing_image_url_raises_transient(self) -> None:
        backend = OpenRouterImageBackend(_config())
        mock = AsyncMock(return_value=_mock_chat_response(images=[{"type": "image_url"}]))
        with _patch_create(backend, mock), pytest.raises(ImageProviderError) as info:
            await backend.generate("a cat")
        assert info.value.context["reason"] == "transient"
