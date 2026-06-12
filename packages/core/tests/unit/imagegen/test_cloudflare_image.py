"""T15 (D-25-11..14) — CloudflareImageBackend unit tests.

Mock Cloudflare Workers AI REST API via ``httpx.MockTransport`` — no real
network. Pins: construction-time validation (provider/key/account_id/allow-set),
the per-model response split (flux JSON+base64 vs SD raw PNG), and the
error-code → domain-exception mapping (R-25-6).
"""

# ruff: noqa: ANN401, ARG001, SLF001
from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from persona.imagegen.cloudflare_image import CloudflareImageBackend
from persona.imagegen.config import ImageBackendConfig
from persona.imagegen.errors import ImageGenUnavailableError, ImageProviderError
from persona.imagegen.protocol import ImageBackend
from pydantic import SecretStr

_PNG = b"\x89PNG\r\n\x1a\n" + b"fakepngbytes"
_FLUX = "@cf/black-forest-labs/flux-1-schnell"
_SDXL = "@cf/stabilityai/stable-diffusion-xl-base-1.0"


def _cfg(model: str, *, key: str = "tok", account: str | None = "acc123") -> ImageBackendConfig:
    return ImageBackendConfig(
        provider="cloudflare",
        model=model,
        api_key=SecretStr(key) if key else None,
        cloudflare_account_id=account,
    )


# --- construction-time validation ---


def test_rejects_wrong_provider() -> None:
    with pytest.raises(ImageProviderError):
        CloudflareImageBackend(
            ImageBackendConfig(provider="openai", model=_FLUX, api_key=SecretStr("k"))
        )


def test_missing_api_key_unavailable() -> None:
    with pytest.raises(ImageGenUnavailableError) as ei:
        CloudflareImageBackend(_cfg(_FLUX, key=""))
    assert ei.value.context["reason"] == "missing_api_key"


def test_missing_account_id_unavailable() -> None:
    with pytest.raises(ImageGenUnavailableError) as ei:
        CloudflareImageBackend(_cfg(_FLUX, account=None))
    assert ei.value.context["reason"] == "missing_account_id"


def test_unknown_model_rejected() -> None:
    with pytest.raises(ImageProviderError) as ei:
        CloudflareImageBackend(_cfg("@cf/leonardo/phoenix-1.0"))
    assert ei.value.context["reason"] == "unsupported_model"


def test_satisfies_protocol() -> None:
    backend = CloudflareImageBackend(_cfg(_FLUX))
    assert isinstance(backend, ImageBackend)
    assert backend.provider_name == "cloudflare"
    assert backend.model_name == _FLUX


def test_url_composition_has_account_and_model() -> None:
    backend = CloudflareImageBackend(_cfg(_FLUX))
    assert backend._url.endswith(f"accounts/acc123/ai/run/{_FLUX}")  # noqa: SLF001


# --- response decode split (patch httpx.AsyncClient to use mock transport) ---


@pytest.fixture
def patch_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch httpx.AsyncClient so backends use a per-test MockTransport."""

    def _install(handler: Any) -> None:
        transport = httpx.MockTransport(handler)
        real_init = httpx.AsyncClient.__init__

        def _init(self: httpx.AsyncClient, *a: Any, **kw: Any) -> None:
            kw["transport"] = transport
            real_init(self, *a, **kw)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)

    return _install


@pytest.mark.asyncio
async def test_flux_json_base64_path(patch_client: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"result": {"image": base64.b64encode(_PNG).decode()}, "success": True},
            headers={"content-type": "application/json"},
        )

    patch_client(handler)
    backend = CloudflareImageBackend(_cfg(_FLUX))
    result = await backend.generate("a cat")
    assert result.provider == "cloudflare"
    assert result.images[0].image_bytes == _PNG


@pytest.mark.asyncio
async def test_sdxl_binary_png_path(patch_client: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PNG, headers={"content-type": "image/png"})

    patch_client(handler)
    backend = CloudflareImageBackend(_cfg(_SDXL))
    result = await backend.generate("a dog")
    assert result.images[0].image_bytes == _PNG


@pytest.mark.asyncio
async def test_429_maps_rate_limit(patch_client: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"success": False, "errors": [{"code": 3036, "message": "daily allocation"}]},
        )

    patch_client(handler)
    backend = CloudflareImageBackend(_cfg(_FLUX))
    with pytest.raises(ImageProviderError) as ei:
        await backend.generate("x")
    assert ei.value.context["reason"] == "rate_limit"


@pytest.mark.asyncio
async def test_403_maps_auth(patch_client: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"success": False, "errors": [{"code": 5018, "message": "no access"}]},
        )

    patch_client(handler)
    backend = CloudflareImageBackend(_cfg(_SDXL))
    with pytest.raises(ImageGenUnavailableError) as ei:
        await backend.generate("x")
    assert ei.value.context["reason"] == "auth"


@pytest.mark.asyncio
async def test_malformed_base64_transient(patch_client: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"result": {"image": "!!!not base64!!!"}},
            headers={"content-type": "application/json"},
        )

    patch_client(handler)
    backend = CloudflareImageBackend(_cfg(_FLUX))
    with pytest.raises(ImageProviderError) as ei:
        await backend.generate("x")
    assert ei.value.context["reason"] == "transient"


# --- T16: factory dispatch + config (D-25-11..14) ---


def test_factory_dispatches_cloudflare() -> None:
    from persona.imagegen._factory import load_image_backend

    backend = load_image_backend(_cfg(_FLUX))
    assert isinstance(backend, CloudflareImageBackend)
    assert backend.provider_name == "cloudflare"


def test_cloudflare_in_image_provider_sets() -> None:
    from persona.imagegen import _factory

    assert "cloudflare" in _factory._IMAGE_PROVIDERS
    assert "cloudflare" in _factory._SUPPORTED_PROVIDERS
    # openrouter (concurrent session's work) must be preserved.
    assert "openrouter" in _factory._IMAGE_PROVIDERS


def test_default_base_url_is_account_prefix() -> None:
    from persona.imagegen.config import DEFAULT_BASE_URLS

    assert DEFAULT_BASE_URLS["cloudflare"].endswith("/accounts/")
