"""``CloudflareImageBackend`` — Cloudflare Workers AI text-to-image (Spec 25).

Implements the :class:`persona.imagegen.protocol.ImageBackend` Protocol against
Cloudflare Workers AI's REST ``/ai/run`` endpoint (D-25-11..14). Structurally a
sibling of :class:`persona.imagegen.nvidia_image.NvidiaImageBackend`'s legacy
branch: raw :mod:`httpx` (no SDK), construction-time fail-fast validation,
provider exceptions caught at the boundary and re-raised as domain errors.

The one wrinkle other providers don't have is a **per-model response split**
(R-25-6): ``flux-1-schnell`` returns JSON ``{"result": {"image": "<base64>"}}``
while the SDXL-family models return raw binary PNG bytes. The runtime
discriminator is the response ``Content-Type`` header, not the model id.

Auth (D-25-12): ``Authorization: Bearer <token>`` + a separate
``cloudflare_account_id`` (NOT embedded in ``base_url``); the request URL is
``{base_url}{account_id}/ai/run/{model}``.
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, Any

import httpx

# NOTE: ``ContentRejectedError`` is intentionally NOT imported/raised — Cloudflare
# Workers AI exposes no synchronous prompt-moderation status (R-25-6 OQ-R6-4);
# a moderated prompt surfaces as a generic 400 → ``reason="transient"``.
from persona.imagegen.errors import ImageGenUnavailableError, ImageProviderError
from persona.imagegen.protocol import ImageBackend as _ImageBackendProtocol
from persona.imagegen.result import GeneratedImage, GenerationResult, ImageGenOptions
from persona.logging import get_logger

if TYPE_CHECKING:
    from persona.imagegen.config import ImageBackendConfig

__all__ = ["CloudflareImageBackend"]

_LOG = get_logger("imagegen.cloudflare")

# Allow-set (D-25-11). The frozensets drive BOTH construction-time validation
# AND the per-model request/response branch. flux returns JSON+base64 and
# takes ``steps`` (max 8); the SD-family return raw PNG and take ``num_steps``
# (max 20) + width/height/guidance/negative_prompt.
_FLUX_MODELS: frozenset[str] = frozenset({"@cf/black-forest-labs/flux-1-schnell"})
_SD_MODELS: frozenset[str] = frozenset(
    {
        "@cf/stabilityai/stable-diffusion-xl-base-1.0",
        "@cf/lykon/dreamshaper-8-lcm",
    }
)
_SUPPORTED: frozenset[str] = _FLUX_MODELS | _SD_MODELS

_FLUX_MAX_STEPS = 8
_SD_MAX_STEPS = 20


def _size_to_wh(size: str) -> tuple[int, int]:
    """Parse an :class:`ImageGenOptions` size preset (``"1024x1024"``) to ints."""
    w, _, h = size.partition("x")
    return int(w), int(h)


class CloudflareImageBackend:
    """Cloudflare Workers AI text-to-image backend (D-25-11..14)."""

    def __init__(self, config: ImageBackendConfig) -> None:
        """Validate config + bind the request URL, failing fast (D-15 precedent).

        Raises:
            ImageProviderError: ``config.provider`` is not ``"cloudflare"``,
                or the model is not in the allow-set
                (``reason="unsupported_model"``).
            ImageGenUnavailableError: missing API token
                (``reason="missing_api_key"``) or missing account id
                (``reason="missing_account_id"``).
        """
        if config.provider != "cloudflare":
            raise ImageProviderError(
                "CloudflareImageBackend requires provider='cloudflare'",
                context={"provider": str(config.provider)},
            )
        key = config.api_key.get_secret_value() if config.api_key else ""
        if not key:
            raise ImageGenUnavailableError(
                "missing Cloudflare API token",
                context={"provider": "cloudflare", "reason": "missing_api_key"},
            )
        if not config.cloudflare_account_id:
            raise ImageGenUnavailableError(
                "missing Cloudflare account_id",
                context={"provider": "cloudflare", "reason": "missing_account_id"},
            )
        if config.model in _FLUX_MODELS:
            self._branch = "flux"
        elif config.model in _SD_MODELS:
            self._branch = "sd"
        else:
            raise ImageProviderError(
                f"unknown Cloudflare model {config.model!r}",
                context={
                    "provider": "cloudflare",
                    "model": config.model,
                    "reason": "unsupported_model",
                    "supported": ", ".join(sorted(_SUPPORTED)),
                },
            )

        from persona.imagegen.config import DEFAULT_BASE_URLS

        base = config.base_url or DEFAULT_BASE_URLS["cloudflare"]
        if not base.endswith("/"):
            base += "/"
        self._url = f"{base}{config.cloudflare_account_id}/ai/run/{config.model}"
        self._model = config.model
        self._key = key
        self._timeout = config.request_timeout_s
        _LOG.debug("constructed", model=self._model, branch=self._branch)

    @property
    def provider_name(self) -> str:
        """Stable provider id echoed into :class:`GenerationResult`."""
        return "cloudflare"

    @property
    def model_name(self) -> str:
        """The configured ``@cf/...`` model id, echoed verbatim."""
        return self._model

    async def generate(
        self,
        prompt: str,
        *,
        options: ImageGenOptions | None = None,
    ) -> GenerationResult:
        """Single-shot generation against Workers AI (atomic per D-20-14).

        Raises:
            ImageGenUnavailableError: 401/403 — token rejected / no model access
                (``reason="auth"``).
            ImageProviderError: rate limit / quota (``reason="rate_limit"``),
                unknown model (``reason="unsupported_model"``), timeout
                (``reason="timeout"``), or transient (``reason="transient"``).
        """
        opts = options or ImageGenOptions()
        # Workers AI ``/ai/run`` returns exactly one image per call. Fail
        # closed on count>1 rather than silently returning one (the honest
        # single-image posture, mirroring OpenRouter's D-22-19 contract).
        if opts.count > 1:
            raise ImageProviderError(
                "cloudflare Workers AI returns one image per call; count>1 is unsupported",
                context={
                    "provider": "cloudflare",
                    "model": self._model,
                    "reason": "unsupported_option",
                    "count": str(opts.count),
                },
            )
        width, height = _size_to_wh(opts.size)
        body = self._build_body(prompt, width, height)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._key}"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ImageProviderError(
                "cloudflare request timed out",
                context={"provider": "cloudflare", "model": self._model, "reason": "timeout"},
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageProviderError(
                "cloudflare transport error",
                context={"provider": "cloudflare", "model": self._model, "reason": "transient"},
            ) from exc

        self._raise_for_status(response)
        image_bytes = self._decode(response)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return GenerationResult(
            images=[
                GeneratedImage(
                    image_bytes=image_bytes,
                    media_type="image/png",
                    width=width,
                    height=height,
                )
            ],
            provider="cloudflare",
            model=self._model,
            latency_ms=latency_ms,
        )

    async def edit(
        self,
        input_image: GeneratedImage,
        instructions: str,
        *,
        options: ImageGenOptions | None = None,
    ) -> GenerationResult:
        """Reserved for v1.x — delegates to the Protocol default."""
        return await _ImageBackendProtocol.edit(self, input_image, instructions, options=options)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_body(self, prompt: str, width: int, height: int) -> dict[str, Any]:
        """Per-model request body — flux uses ``steps``; SD uses ``num_steps``."""
        if self._branch == "flux":
            # flux-1-schnell: prompt + steps only (no width/height/guidance).
            return {"prompt": prompt, "steps": min(4, _FLUX_MAX_STEPS)}
        return {
            "prompt": prompt,
            "num_steps": min(20, _SD_MAX_STEPS),
            "width": width,
            "height": height,
        }

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map a non-2xx Cloudflare v4 envelope to a domain error (R-25-6)."""
        if response.status_code < 400:
            return
        code: int | None = None
        message = ""
        try:
            payload = response.json()
            errors = payload.get("errors") or []
            if errors:
                code = errors[0].get("code")
                message = str(errors[0].get("message", ""))
        except (ValueError, AttributeError, KeyError, TypeError):
            message = response.text[:200]
        ctx = {"provider": "cloudflare", "model": self._model}
        status = response.status_code
        if status in (401, 403) or code == 10000:
            raise ImageGenUnavailableError(
                f"cloudflare rejected credentials/model access: {message}",
                context={**ctx, "reason": "auth"},
            )
        if code in (5007, 3042):
            raise ImageProviderError(
                f"cloudflare reports unknown model: {message}",
                context={**ctx, "reason": "unsupported_model"},
            )
        if status == 429:
            raise ImageProviderError(
                f"cloudflare rate limit / neuron quota: {message}",
                context={**ctx, "reason": "rate_limit"},
            )
        if status == 408:
            raise ImageProviderError(
                f"cloudflare request timeout: {message}",
                context={**ctx, "reason": "timeout"},
            )
        raise ImageProviderError(
            f"cloudflare provider error (HTTP {status}): {message}",
            context={**ctx, "reason": "transient"},
        )

    def _decode(self, response: httpx.Response) -> bytes:
        """Content-type-branched decode: JSON+base64 (flux) vs raw PNG (SD)."""
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                b64 = response.json()["result"]["image"]
                return base64.b64decode(b64, validate=True)
            except (ValueError, TypeError, KeyError) as exc:
                raise ImageProviderError(
                    "cloudflare returned malformed JSON/base64 image data",
                    context={"provider": "cloudflare", "model": self._model, "reason": "transient"},
                ) from exc
        return response.content
