"""Tests for ``persona.imagegen.fal_image`` (Spec 15 T07).

Mocked-SDK unit tests for :class:`FalImageBackend`: the happy path, the
D-15-X-flagged-image-policy `has_nsfw_concepts` refusal, the four
error-mapping branches (auth / rate limit / input moderation / transient
5xx), the size-preset mapping, and the bytes-download-from-CDN
provenance using :class:`httpx.MockTransport` (mirror of the Spec 13
fixture pattern). No real network calls; no real fal credentials.

The fal SDK is real (installed via ``fal-client>=1.0,<2``); we
``patch.object`` :meth:`FalImageBackend._subscribe` to inject canned
responses without touching the upstream queue.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest
from persona.imagegen import (
    ContentRejectedError,
    ImageBackend,
    ImageBackendConfig,
    ImageGenOptions,
    ImageGenUnavailableError,
    ImageProviderError,
)
from persona.imagegen.fal_image import (
    _FAL_IMAGE_CAPABILITY,
    _SIZE_DIMENSIONS,
    FalImageBackend,
)
from pydantic import SecretStr

# Capture the real ``httpx.AsyncClient`` at import time so the lambda
# factories used to inject ``MockTransport`` in tests below do not
# recurse into themselves when ``persona.imagegen.fal_image.httpx.AsyncClient``
# is patched (the patched attribute and ``httpx.AsyncClient`` reference
# the same module-level object, so a naive lambda would loop forever and
# raise ``TypeError: got multiple values for keyword argument 'transport'``).
_REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def valid_config() -> ImageBackendConfig:
    """A config that satisfies fail-fast at construction."""
    return ImageBackendConfig(
        provider="fal",
        model="fal-ai/flux-pro/v1.1",
        api_key=SecretStr("test-key"),
    )


@pytest.fixture
def png_bytes() -> bytes:
    """A tiny PNG byte payload — the magic header + body."""
    return b"\x89PNG\r\n\x1a\nfake-image-bytes-for-tests"


def _mock_transport_for_url(url: str, body: bytes) -> httpx.MockTransport:
    """Build an httpx.MockTransport that responds with ``body`` for ``url``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == url:
            return httpx.Response(200, content=body)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


class TestConstruction:
    """Construction-time fail-fast contract."""

    def test_construct_with_valid_config(self, valid_config: ImageBackendConfig) -> None:
        backend = FalImageBackend(valid_config)
        assert backend.provider_name == "fal"
        assert backend.model_name == "fal-ai/flux-pro/v1.1"

    def test_protocol_membership(self, valid_config: ImageBackendConfig) -> None:
        backend = FalImageBackend(valid_config)
        assert isinstance(backend, ImageBackend)

    def test_missing_api_key_raises_unavailable(self) -> None:
        config = ImageBackendConfig(
            provider="fal",
            model="fal-ai/flux-pro/v1.1",
            api_key=None,
        )
        with pytest.raises(ImageGenUnavailableError) as exc_info:
            FalImageBackend(config)
        assert exc_info.value.context["reason"] == "missing_api_key"

    def test_empty_api_key_raises_unavailable(self) -> None:
        config = ImageBackendConfig(
            provider="fal",
            model="fal-ai/flux-pro/v1.1",
            api_key=SecretStr(""),
        )
        with pytest.raises(ImageGenUnavailableError) as exc_info:
            FalImageBackend(config)
        assert exc_info.value.context["reason"] == "missing_api_key"

    def test_wrong_provider_raises_provider_error(self) -> None:
        # Belt-and-braces guard for the direct-construction path; the
        # factory should never dispatch a non-fal config here.
        config = ImageBackendConfig(
            provider="openai",
            model="gpt-image-1",
            api_key=SecretStr("test-key"),
        )
        with pytest.raises(ImageProviderError) as exc_info:
            FalImageBackend(config)
        assert exc_info.value.context["provider"] == "openai"


class TestCapabilityMatrix:
    """The co-located ``_FAL_IMAGE_CAPABILITY`` map."""

    def test_flux_pro_in_capability_matrix(self) -> None:
        assert "fal-ai/flux-pro/v1.1" in _FAL_IMAGE_CAPABILITY

    def test_capability_set_is_frozen(self) -> None:
        # Mirror discipline — the Spec 02 capability sets are frozensets
        # so extending the matrix is a typed code change.
        for capability in _FAL_IMAGE_CAPABILITY.values():
            if capability != "all":
                assert isinstance(capability, frozenset)

    @pytest.mark.parametrize("size", ["1024x1024", "1024x1792", "1792x1024"])
    def test_all_three_neutral_presets_supported(self, size: str) -> None:
        capability = _FAL_IMAGE_CAPABILITY["fal-ai/flux-pro/v1.1"]
        assert capability != "all"
        assert size in capability

    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            ("1024x1024", (1024, 1024)),
            ("1024x1792", (1024, 1792)),
            ("1792x1024", (1792, 1024)),
        ],
    )
    def test_size_dimensions_table(self, size: str, expected: tuple[int, int]) -> None:
        # fal accepts arbitrary (width, height) on Flux 1.1 [pro]; no
        # rounding applies (research §2.3) — that's OpenAI-only via
        # D-15-X-size-rounding.
        assert _SIZE_DIMENSIONS[size] == expected


class TestGenerateHappyPath:
    """The happy path through generate()."""

    @pytest.mark.asyncio
    async def test_returns_generation_result_with_one_image(
        self, valid_config: ImageBackendConfig, png_bytes: bytes
    ) -> None:
        backend = FalImageBackend(valid_config)
        cdn_url = "https://fal.media/files/x/abc123.png"
        fake_response: dict[str, Any] = {
            "images": [
                {
                    "url": cdn_url,
                    "width": 1024,
                    "height": 1024,
                    "content_type": "image/png",
                }
            ],
            "prompt": "a red bicycle",
            "seed": 42,
            "has_nsfw_concepts": [False],
            "timings": {"inference": 4.21},
        }

        async def fake_subscribe(arguments: dict[str, Any]) -> dict[str, Any]:
            assert arguments["prompt"] == "a red bicycle"
            assert arguments["image_size"] == {"width": 1024, "height": 1024}
            assert arguments["num_images"] == 1
            assert arguments["safety_tolerance"] == "2"
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, png_bytes), **kw
                ),
            ),
        ):
            result = await backend.generate("a red bicycle")

        assert result.provider == "fal"
        assert result.model == "fal-ai/flux-pro/v1.1"
        assert len(result.images) == 1
        assert result.images[0].image_bytes == png_bytes
        assert result.images[0].media_type == "image/png"
        assert result.images[0].width == 1024
        assert result.images[0].height == 1024
        assert result.images[0].revised_prompt == "a red bicycle"
        assert result.latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_count_two_returns_two_images(
        self, valid_config: ImageBackendConfig, png_bytes: bytes
    ) -> None:
        backend = FalImageBackend(valid_config)
        cdn_url_a = "https://fal.media/files/x/a.png"
        cdn_url_b = "https://fal.media/files/x/b.png"
        fake_response: dict[str, Any] = {
            "images": [
                {"url": cdn_url_a, "width": 1024, "height": 1024, "content_type": "image/png"},
                {"url": cdn_url_b, "width": 1024, "height": 1024, "content_type": "image/png"},
            ],
            "prompt": "a cat",
            "has_nsfw_concepts": [False, False],
        }

        async def fake_subscribe(arguments: dict[str, Any]) -> dict[str, Any]:
            assert arguments["num_images"] == 2
            return fake_response

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) in {cdn_url_a, cdn_url_b}:
                return httpx.Response(200, content=png_bytes)
            return httpx.Response(404)

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw),
            ),
        ):
            result = await backend.generate(
                "a cat", options=ImageGenOptions(size="1024x1024", count=2, quality="standard")
            )

        assert len(result.images) == 2
        assert all(img.image_bytes == png_bytes for img in result.images)

    @pytest.mark.asyncio
    async def test_quality_noop_does_not_crash(
        self, valid_config: ImageBackendConfig, png_bytes: bytes
    ) -> None:
        # Flux 1.1 [pro] has no quality dial; the backend logs + ignores.
        backend = FalImageBackend(valid_config)
        cdn_url = "https://fal.media/files/x/q.png"
        fake_response: dict[str, Any] = {
            "images": [
                {"url": cdn_url, "width": 1024, "height": 1024, "content_type": "image/png"}
            ],
            "prompt": "a cat",
            "has_nsfw_concepts": [False],
        }

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, png_bytes), **kw
                ),
            ),
        ):
            result = await backend.generate("a cat", options=ImageGenOptions(quality="standard"))
        assert len(result.images) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("size", "expected_w", "expected_h"),
        [
            ("1024x1024", 1024, 1024),
            ("1024x1792", 1024, 1792),
            ("1792x1024", 1792, 1024),
        ],
    )
    async def test_size_mapping_passes_through(
        self,
        valid_config: ImageBackendConfig,
        png_bytes: bytes,
        size: str,
        expected_w: int,
        expected_h: int,
    ) -> None:
        # fal accepts arbitrary (w, h) — no rounding (research §2.3).
        backend = FalImageBackend(valid_config)
        cdn_url = "https://fal.media/files/x/s.png"
        fake_response: dict[str, Any] = {
            "images": [
                {
                    "url": cdn_url,
                    "width": expected_w,
                    "height": expected_h,
                    "content_type": "image/png",
                }
            ],
            "prompt": "a cat",
            "has_nsfw_concepts": [False],
        }
        captured: dict[str, Any] = {}

        async def fake_subscribe(arguments: dict[str, Any]) -> dict[str, Any]:
            captured["image_size"] = arguments["image_size"]
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, png_bytes), **kw
                ),
            ),
        ):
            await backend.generate("a cat", options=ImageGenOptions(size=cast("Any", size)))

        assert captured["image_size"] == {"width": expected_w, "height": expected_h}


class TestFlaggedImagePolicy:
    """D-15-X-flagged-image-policy: any ``has_nsfw_concepts[i] = True`` refuses
    the whole call as ``ContentRejectedError(reason="provider_post_gen_moderation",
    stage="output")``. Bytes are NOT downloaded."""

    @pytest.mark.asyncio
    async def test_single_flagged_refuses_whole_call(
        self, valid_config: ImageBackendConfig
    ) -> None:
        backend = FalImageBackend(valid_config)
        fake_response: dict[str, Any] = {
            "images": [
                {
                    "url": "https://fal.media/files/x/blocked.png",
                    "width": 1024,
                    "height": 1024,
                    "content_type": "image/png",
                }
            ],
            "prompt": "a thing",
            "has_nsfw_concepts": [True],
        }

        download_called = False

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal download_called
            download_called = True
            return httpx.Response(200, content=b"should-not-be-fetched")

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw),
            ),
            pytest.raises(ContentRejectedError) as exc_info,
        ):
            await backend.generate("a thing")

        assert exc_info.value.context["reason"] == "provider_post_gen_moderation"
        assert exc_info.value.context["stage"] == "output"
        assert exc_info.value.context["flagged_count"] == "1"
        # Critical: the bytes are NEVER downloaded when a flag triggers.
        assert not download_called

    @pytest.mark.asyncio
    async def test_any_flagged_in_batch_refuses_all(self, valid_config: ImageBackendConfig) -> None:
        # D-15-X-flagged-image-policy option (a): whole call refused
        # regardless of which image in the batch is flagged.
        backend = FalImageBackend(valid_config)
        fake_response: dict[str, Any] = {
            "images": [
                {"url": "https://fal.media/a", "width": 1024, "height": 1024},
                {"url": "https://fal.media/b", "width": 1024, "height": 1024},
            ],
            "prompt": "a thing",
            "has_nsfw_concepts": [False, True],
        }

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ContentRejectedError) as exc_info,
        ):
            await backend.generate("a thing", options=ImageGenOptions(count=2))

        assert exc_info.value.context["flagged_count"] == "1"

    @pytest.mark.asyncio
    async def test_no_flags_proceeds_normally(
        self, valid_config: ImageBackendConfig, png_bytes: bytes
    ) -> None:
        backend = FalImageBackend(valid_config)
        cdn_url = "https://fal.media/files/x/ok.png"
        fake_response: dict[str, Any] = {
            "images": [
                {"url": cdn_url, "width": 1024, "height": 1024, "content_type": "image/png"}
            ],
            "prompt": "a cat",
            "has_nsfw_concepts": [False],
        }

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, png_bytes), **kw
                ),
            ),
        ):
            result = await backend.generate("a cat")
        assert result.images[0].image_bytes == png_bytes

    @pytest.mark.asyncio
    async def test_missing_has_nsfw_concepts_proceeds(
        self, valid_config: ImageBackendConfig, png_bytes: bytes
    ) -> None:
        # If fal omits the field (unlikely per the SDK contract but
        # defensive), the backend should NOT refuse — only a list with
        # at least one True triggers refusal.
        backend = FalImageBackend(valid_config)
        cdn_url = "https://fal.media/files/x/no-flag-key.png"
        fake_response: dict[str, Any] = {
            "images": [
                {"url": cdn_url, "width": 1024, "height": 1024, "content_type": "image/png"}
            ],
            "prompt": "a cat",
        }

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, png_bytes), **kw
                ),
            ),
        ):
            result = await backend.generate("a cat")
        assert len(result.images) == 1


class TestErrorMapping:
    """Adapter-boundary error funnel into the domain exception hierarchy."""

    @pytest.mark.asyncio
    async def test_http_401_auth_maps_to_unavailable(
        self, valid_config: ImageBackendConfig
    ) -> None:
        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            request = httpx.Request("POST", "https://queue.fal.run/x")
            response = httpx.Response(401, request=request, json={"detail": "Invalid API key"})
            raise httpx.HTTPStatusError("auth", request=request, response=response)

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageGenUnavailableError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "auth"

    @pytest.mark.asyncio
    async def test_http_429_rate_limit_maps_to_provider_error(
        self, valid_config: ImageBackendConfig
    ) -> None:
        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            request = httpx.Request("POST", "https://queue.fal.run/x")
            response = httpx.Response(429, request=request, headers={"retry-after": "30"}, json={})
            raise httpx.HTTPStatusError("rl", request=request, response=response)

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "rate_limit"
        assert exc_info.value.context["retry_after_s"] == "30"

    @pytest.mark.asyncio
    async def test_http_422_input_moderation_maps_to_content_rejected(
        self, valid_config: ImageBackendConfig
    ) -> None:
        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            request = httpx.Request("POST", "https://queue.fal.run/x")
            response = httpx.Response(
                422,
                request=request,
                json={"detail": [{"type": "value_error", "msg": "Content policy violation"}]},
            )
            raise httpx.HTTPStatusError("422", request=request, response=response)

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ContentRejectedError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "provider_moderation"
        assert exc_info.value.context["stage"] == "input"

    @pytest.mark.asyncio
    async def test_http_500_transient_maps_to_provider_error(
        self, valid_config: ImageBackendConfig
    ) -> None:
        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            request = httpx.Request("POST", "https://queue.fal.run/x")
            response = httpx.Response(500, request=request, json={})
            raise httpx.HTTPStatusError("5xx", request=request, response=response)

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "transient"
        assert exc_info.value.context["status"] == "500"

    @pytest.mark.asyncio
    async def test_timeout_maps_to_provider_error_timeout(
        self, valid_config: ImageBackendConfig
    ) -> None:
        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            raise httpx.TimeoutException("timed out")

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "timeout"

    @pytest.mark.asyncio
    async def test_generic_httpx_error_maps_to_transient(
        self, valid_config: ImageBackendConfig
    ) -> None:
        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            raise httpx.ConnectError("connection refused")

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_fal_client_timeout_error_by_name(self, valid_config: ImageBackendConfig) -> None:
        # Class-name-based dispatch — synthesise an exception with the
        # SDK class name without importing the SDK module.
        class FalClientTimeoutError(Exception):
            pass

        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            raise FalClientTimeoutError("timed out")

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "timeout"

    @pytest.mark.asyncio
    async def test_fal_client_http_401_by_name(self, valid_config: ImageBackendConfig) -> None:
        class FalClientHTTPError(Exception):
            def __init__(self, msg: str, status_code: int) -> None:
                super().__init__(msg)
                self.status_code = status_code

        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            raise FalClientHTTPError("auth", 401)

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageGenUnavailableError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "auth"

    @pytest.mark.asyncio
    async def test_fal_client_http_422_by_name(self, valid_config: ImageBackendConfig) -> None:
        class FalClientHTTPError(Exception):
            def __init__(self, msg: str, status_code: int) -> None:
                super().__init__(msg)
                self.status_code = status_code

        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            raise FalClientHTTPError("content policy", 422)

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ContentRejectedError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "provider_moderation"
        assert exc_info.value.context["stage"] == "input"

    @pytest.mark.asyncio
    async def test_unsupported_size_raises_before_subscribe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Construct with a model that has an empty capability set so the
        # adapter's pre-call guard fires.
        monkeypatch.setitem(_FAL_IMAGE_CAPABILITY, "fal-ai/no-such-model", frozenset())
        config = ImageBackendConfig(
            provider="fal", model="fal-ai/no-such-model", api_key=SecretStr("k")
        )
        backend = FalImageBackend(config)

        called = False

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            nonlocal called
            called = True
            return {}

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat", options=ImageGenOptions(size="1024x1024"))
        assert exc_info.value.context["reason"] == "unsupported_option"
        assert exc_info.value.context["size"] == "1024x1024"
        assert not called


class TestResponseShapeValidation:
    """Defensive guards on the fal response shape."""

    @pytest.mark.asyncio
    async def test_no_images_raises_transient(self, valid_config: ImageBackendConfig) -> None:
        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return {"images": [], "prompt": "a cat", "has_nsfw_concepts": []}

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_missing_url_raises_transient(self, valid_config: ImageBackendConfig) -> None:
        backend = FalImageBackend(valid_config)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return {
                "images": [{"width": 1024, "height": 1024, "content_type": "image/png"}],
                "prompt": "a cat",
                "has_nsfw_concepts": [False],
            }

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_non_dict_response_raises_transient(
        self, valid_config: ImageBackendConfig
    ) -> None:
        backend = FalImageBackend(valid_config)

        async def fake_subscribe_raw(
            _application: str,
            _arguments: dict[str, Any],
            **_kwargs: object,
        ) -> list[str]:
            return ["not-a-dict"]

        # Patch the SDK-level subscribe_async so backend._subscribe runs
        # against the bad shape directly.
        with (
            patch("fal_client.subscribe_async", new=fake_subscribe_raw),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_unknown_content_type_defaults_to_png(
        self, valid_config: ImageBackendConfig, png_bytes: bytes
    ) -> None:
        backend = FalImageBackend(valid_config)
        cdn_url = "https://fal.media/files/x/unknown.bin"
        fake_response: dict[str, Any] = {
            "images": [
                {
                    "url": cdn_url,
                    "width": 1024,
                    "height": 1024,
                    "content_type": "application/octet-stream",
                }
            ],
            "prompt": "a cat",
            "has_nsfw_concepts": [False],
        }

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, png_bytes), **kw
                ),
            ),
        ):
            result = await backend.generate("a cat")
        # Unknown media types default to image/png so the boundary
        # Literal stays valid; the actual bytes are still served as-is.
        assert result.images[0].media_type == "image/png"


class TestCDNDownload:
    """Bytes-download-from-CDN provenance via httpx.MockTransport (Spec 13 pattern)."""

    @pytest.mark.asyncio
    async def test_cdn_404_raises_transient(self, valid_config: ImageBackendConfig) -> None:
        backend = FalImageBackend(valid_config)
        # The MockTransport returns 404 for any URL not matching its
        # registered URL — register a different URL to force a miss.
        cdn_url = "https://fal.media/files/x/real.png"
        bad_url = "https://fal.media/files/x/wrong.png"
        fake_response: dict[str, Any] = {
            "images": [
                {"url": bad_url, "width": 1024, "height": 1024, "content_type": "image/png"}
            ],
            "prompt": "a cat",
            "has_nsfw_concepts": [False],
        }

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, b"x"), **kw
                ),
            ),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        # Mapped through _reraise as transient (httpx 404 → HTTPStatusError).
        assert exc_info.value.context["reason"] == "transient"

    @pytest.mark.asyncio
    async def test_cdn_empty_body_raises_transient(self, valid_config: ImageBackendConfig) -> None:
        backend = FalImageBackend(valid_config)
        cdn_url = "https://fal.media/files/x/empty.png"
        fake_response: dict[str, Any] = {
            "images": [
                {"url": cdn_url, "width": 1024, "height": 1024, "content_type": "image/png"}
            ],
            "prompt": "a cat",
            "has_nsfw_concepts": [False],
        }

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return fake_response

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, b""), **kw
                ),
            ),
            pytest.raises(ImageProviderError) as exc_info,
        ):
            await backend.generate("a cat")
        assert exc_info.value.context["reason"] == "transient"


class TestSafetyToleranceWiring:
    """Per-config ``fal_safety_tolerance`` flows into the request arguments."""

    @pytest.mark.asyncio
    async def test_default_safety_tolerance_is_2(
        self, valid_config: ImageBackendConfig, png_bytes: bytes
    ) -> None:
        # D-15-X-provider-moderation-default — fal default is "2".
        backend = FalImageBackend(valid_config)
        cdn_url = "https://fal.media/files/x/st.png"
        captured: dict[str, Any] = {}

        async def fake_subscribe(arguments: dict[str, Any]) -> dict[str, Any]:
            captured["safety_tolerance"] = arguments["safety_tolerance"]
            return {
                "images": [
                    {
                        "url": cdn_url,
                        "width": 1024,
                        "height": 1024,
                        "content_type": "image/png",
                    }
                ],
                "prompt": "a cat",
                "has_nsfw_concepts": [False],
            }

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, png_bytes), **kw
                ),
            ),
        ):
            await backend.generate("a cat")
        assert captured["safety_tolerance"] == "2"

    @pytest.mark.asyncio
    async def test_explicit_safety_tolerance_propagates(self, png_bytes: bytes) -> None:
        config = ImageBackendConfig(
            provider="fal",
            model="fal-ai/flux-pro/v1.1",
            api_key=SecretStr("k"),
            fal_safety_tolerance=5,
        )
        backend = FalImageBackend(config)
        cdn_url = "https://fal.media/files/x/st5.png"
        captured: dict[str, Any] = {}

        async def fake_subscribe(arguments: dict[str, Any]) -> dict[str, Any]:
            captured["safety_tolerance"] = arguments["safety_tolerance"]
            return {
                "images": [
                    {
                        "url": cdn_url,
                        "width": 1024,
                        "height": 1024,
                        "content_type": "image/png",
                    }
                ],
                "prompt": "a cat",
                "has_nsfw_concepts": [False],
            }

        with (
            patch.object(backend, "_subscribe", new=fake_subscribe),
            patch(
                "persona.imagegen.fal_image.httpx.AsyncClient",
                lambda **kw: _REAL_ASYNC_CLIENT(
                    transport=_mock_transport_for_url(cdn_url, png_bytes), **kw
                ),
            ),
        ):
            await backend.generate("a cat")
        assert captured["safety_tolerance"] == "5"
