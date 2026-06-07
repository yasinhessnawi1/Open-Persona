"""Flux 1.1 [pro] image backend via fal.ai (Spec 15 T07).

Implements :class:`persona.imagegen.protocol.ImageBackend` against the
fal.ai queue endpoint for ``fal-ai/flux-pro/v1.1`` (D-15-1). The backend
calls ``fal_client.subscribe_async(...)`` and unwraps the queue response
into the neutral :class:`GenerationResult` boundary type — the CDN URL
fal returns is downloaded to in-memory bytes via :class:`httpx.AsyncClient`
(Spec 13 pattern) so the service layer (T15) lands the bytes through the
same ``resolve_sandbox_path`` + ``O_NOFOLLOW`` write path as uploaded
images.

Three concerns are load-bearing here:

* **Mirror Spec 02 openai_compat.py shape.** Co-located capability matrix
  near the top of the file (``_FAL_IMAGE_CAPABILITY``); construction-time
  fail-fast on missing API key; adapter-boundary error-mapping into the
  :class:`persona.imagegen.errors` domain hierarchy. Per the Spec 15
  decisions gate paragraph #1 mirror discipline.
* **Lazy SDK import.** The ``fal_client`` import lives inside the
  constructor body so ``persona.imagegen`` stays importable on a clean
  ``pip install persona-core`` without ``fal-client``. Same pattern the
  factory uses (lazy ``import persona.imagegen.fal_image`` inside
  :func:`load_image_backend`) but one layer down — at the SDK boundary.
* **D-15-X-flagged-image-policy.** When fal returns ``has_nsfw_concepts``
  with any ``True`` element, the whole call is refused as
  :class:`ContentRejectedError(reason="provider_post_gen_moderation",
  stage="output")`. The bytes are NOT downloaded; the service layer
  refunds credits.

References:
    docs/specs/phase2/spec_15/decisions.md gate paragraph #1 + D-15-1 +
    D-15-X-flagged-image-policy + D-15-X-provider-moderation-default +
    D-15-X-license-stack; research.md §2.2 for the API contract +
    §1.1 for the cost/latency profile.
"""

from __future__ import annotations

import time
from typing import Any, Literal, NoReturn, cast

import httpx

from persona.imagegen.config import DEFAULT_BASE_URLS, ImageBackendConfig
from persona.imagegen.errors import (
    ContentRejectedError,
    ImageGenUnavailableError,
    ImageProviderError,
)
from persona.imagegen.result import (
    GeneratedImage,
    GenerationResult,
    ImageGenOptions,
    ImageMediaType,
)
from persona.logging import get_logger

__all__ = ["FalImageBackend"]


_LOG = get_logger("imagegen.fal_image")


# Per-model capability matrix (co-located with the backend per the Spec 02
# ``openai_compat.py:_NATIVE_TOOLS_CAPABILITY`` mirror discipline). Mirrors
# the spec 15 decisions gate paragraph #1: each backend owns its own
# capability matrix; the surface stays extensible without touching the
# Protocol or the factory. fal.ai accepts custom ``{width, height}`` pairs
# on Flux 1.1 [pro] so the matrix records the closed neutral preset set the
# adapter is willing to forward.
_FAL_IMAGE_CAPABILITY: dict[str, frozenset[str] | Literal["all"]] = {
    "fal-ai/flux-pro/v1.1": frozenset({"1024x1024", "1024x1792", "1792x1024"}),
}
"""Closed model-id → supported neutral-preset capability map (D-15-1).

Unknown models default to an empty frozenset; the adapter raises
:class:`ImageProviderError(reason="unsupported_option")` rather than
silently passing through an arbitrary size to the upstream queue.
Extending the matrix is a one-line change when fal.ai adds new Flux model
variants or new size presets.
"""


# Neutral-preset → fal ``image_size`` dict mapping per research.md §2.3.
# fal accepts arbitrary ``{width, height}`` (no rounding); the OpenAI
# rounding (D-15-X-size-rounding) is OpenAI-only.
_SIZE_DIMENSIONS: dict[str, tuple[int, int]] = {
    "1024x1024": (1024, 1024),
    "1024x1792": (1024, 1792),
    "1792x1024": (1792, 1024),
}


# IANA media-type lookup for the fal response ``content_type`` field. The
# ``image_size`` Literal restriction keeps the closed surface in lockstep
# with :data:`persona.imagegen.result.ImageMediaType`.
_MEDIA_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/webp"})


def _size_supported(model: str, size: str) -> bool:
    """Return True iff ``size`` is in the capability set for ``model``."""
    capability = _FAL_IMAGE_CAPABILITY.get(model, frozenset())
    if capability == "all":
        return True
    assert isinstance(capability, frozenset)
    return size in capability


class FalImageBackend:
    """Async Flux 1.1 [pro] image backend via fal.ai.

    The SDK (``fal_client``) is lazy-imported inside ``__init__`` so the
    module stays importable on a clean ``pip install persona-core``
    without ``fal-client`` — construction raises
    :class:`ImageGenUnavailableError` with a clear ``reason="sdk_missing"``
    when the SDK is absent (same fail-loud shape Spec 02 ``hf_local``
    uses).

    The constructor also fail-fasts on missing ``api_key`` per the
    Spec 02 D-02-* construction-time-fail-fast precedent — callers see
    the unavailability at startup, not on first call.

    References:
        docs/specs/phase2/spec_15/decisions.md gate paragraph #1 +
        D-15-1 + D-15-X-flagged-image-policy.
    """

    def __init__(self, config: ImageBackendConfig) -> None:
        """Construct and validate. Fail-fast on missing key or absent SDK.

        Args:
            config: The image backend configuration. ``config.provider``
                must be ``"fal"``; ``config.api_key`` must be a populated
                :class:`pydantic.SecretStr` (empty string raises
                :class:`ImageGenUnavailableError`).

        Raises:
            ImageGenUnavailableError: ``config.api_key`` is ``None`` or
                empty, OR the ``fal-client`` package is not installed.
            ImageProviderError: ``config.provider`` is not ``"fal"`` (the
                factory should never dispatch a non-fal config here, but
                belt-and-braces guards the direct-construction path).
        """
        if config.provider != "fal":
            msg = (
                f"FalImageBackend does not handle provider {config.provider!r}; "
                "use load_image_backend() to dispatch."
            )
            raise ImageProviderError(msg, context={"provider": config.provider})

        if config.api_key is None or not config.api_key.get_secret_value():
            raise ImageGenUnavailableError(
                "missing fal.ai API key",
                context={"provider": "fal", "reason": "missing_api_key"},
            )

        # Lazy-import the SDK at construction so ``persona.imagegen`` and
        # ``persona.imagegen.fal_image`` import cleanly on a minimal
        # ``pip install persona-core`` — the SDK is a Spec-15-only dep
        # declared in ``packages/core/pyproject.toml``. The fail-fast on
        # missing SDK preserves the construction-time-fail-fast precedent.
        try:
            import fal_client  # noqa: F401 — import-only for availability check
        except ImportError as exc:  # pragma: no cover — exercised by sdk-missing test
            raise ImageGenUnavailableError(
                "fal-client SDK not installed; "
                "install with `pip install 'persona-core[imagegen]'` or "
                "`pip install fal-client>=1.0,<2`",
                context={"provider": "fal", "reason": "sdk_missing"},
            ) from exc

        self._config = config
        self._provider = "fal"
        self._model = config.model
        self._timeout = config.request_timeout_s
        self._safety_tolerance = str(config.fal_safety_tolerance)
        self._api_key = config.api_key.get_secret_value()
        self._base_url = config.base_url or DEFAULT_BASE_URLS.get("fal")

        _LOG.debug(
            "constructed",
            provider=self._provider,
            model=self._model,
            safety_tolerance=self._safety_tolerance,
        )

    @property
    def provider_name(self) -> str:
        """The ``"fal"`` provider identifier (lowercase, ASCII, stable)."""
        return self._provider

    @property
    def model_name(self) -> str:
        """Echo of the configured model (e.g. ``"fal-ai/flux-pro/v1.1"``)."""
        return self._model

    async def generate(
        self,
        prompt: str,
        *,
        options: ImageGenOptions | None = None,
    ) -> GenerationResult:
        """Call ``fal_client.subscribe_async`` and unwrap the response.

        Maps the neutral :class:`ImageGenOptions` shape into the fal-
        specific request arguments per research.md §2.3:

        * ``size`` → ``image_size`` as a ``{width, height}`` dict (fal
          accepts arbitrary dimensions on Flux 1.1 [pro] so no rounding
          applies — that is OpenAI-only via D-15-X-size-rounding).
        * ``count`` → ``num_images``.
        * ``quality`` → no-op + debug log (Flux 1.1 [pro] is a
          single-step optimised model; no quality dial). Documented in
          :class:`persona.imagegen.result.ImageQuality`.

        Downloads bytes from each ``images[i].url`` via httpx in the same
        call (the fal CDN URLs expire; the backend persists in-memory
        bytes that the service layer (T15) writes to the workspace).

        Args:
            prompt: The merged text prompt (visual_style merging happens
                upstream in T11 ``_merge.py``).
            options: Optional neutral knobs. ``None`` means the default
                :class:`ImageGenOptions`.

        Returns:
            :class:`GenerationResult` with one or more
            :class:`GeneratedImage` instances each carrying
            :attr:`GeneratedImage.image_bytes` populated. The service
            layer (T15) writes the bytes to disk and zeroes the field
            for the response envelope.

        Raises:
            ImageGenUnavailableError: fal rejected the API key (401/403).
            ImageProviderError: rate limit, transient 5xx, unsupported
                ``(model, size)`` pair, or request timeout.
            ContentRejectedError: input moderation (HTTP 422 with
                content-policy body, ``reason="provider_moderation"`` +
                ``stage="input"``) or post-generation flag
                (``has_nsfw_concepts`` contains True,
                ``reason="provider_post_gen_moderation"`` +
                ``stage="output"`` per D-15-X-flagged-image-policy).
        """
        opts = options or ImageGenOptions()

        if not _size_supported(self._model, opts.size):
            raise ImageProviderError(
                "size not supported by this model",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "unsupported_option",
                    "size": opts.size,
                },
            )

        width, height = _SIZE_DIMENSIONS[opts.size]
        arguments: dict[str, Any] = {
            "prompt": prompt,
            "image_size": {"width": width, "height": height},
            "num_images": opts.count,
            "safety_tolerance": self._safety_tolerance,
        }

        if opts.quality != "high":
            _LOG.debug(
                "quality-noop",
                provider=self._provider,
                model=self._model,
                requested_quality=opts.quality,
                note="Flux 1.1 [pro] is single-step; no quality dial",
            )

        started = time.perf_counter()
        try:
            response = await self._subscribe(arguments)
        except Exception as exc:  # caught at adapter boundary; re-raised as domain
            self._reraise(exc)

        # D-15-X-flagged-image-policy: any `has_nsfw_concepts[i] = True`
        # refuses the WHOLE call. Bytes are NOT downloaded; credits are
        # refunded by the service layer (T15).
        flagged = response.get("has_nsfw_concepts")
        if isinstance(flagged, list) and any(bool(x) for x in flagged):
            raise ContentRejectedError(
                "fal flagged the generated image(s) as nsfw",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "provider_post_gen_moderation",
                    "stage": "output",
                    "flagged_count": str(sum(1 for x in flagged if x)),
                },
            )

        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            images = await self._download_images(response)
        except (
            ImageGenUnavailableError,
            ImageProviderError,
            ContentRejectedError,
        ):
            # Domain exceptions raised by ``_download_images`` itself
            # (e.g. response-shape guards) bubble unchanged.
            raise
        except Exception as exc:  # CDN transport failures funnel through _reraise
            self._reraise(exc)
        revised_prompt = response.get("prompt")
        if revised_prompt is not None and not isinstance(revised_prompt, str):
            revised_prompt = None

        generated = [
            img.model_copy(update={"revised_prompt": revised_prompt}) if revised_prompt else img
            for img in images
        ]

        return GenerationResult(
            images=generated,
            provider=self._provider,
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
        """Edit an existing image — RESERVED for v1.x (D-15-X-edit-protocol-reservation).

        v1 concrete backends do NOT implement this; the method delegates
        to the Protocol default which raises :class:`NotImplementedError`.
        Surfaced on the class only so :func:`isinstance` against the
        :class:`persona.imagegen.protocol.ImageBackend`
        ``@runtime_checkable`` Protocol returns True.
        """
        from persona.imagegen.protocol import ImageBackend as _Proto

        return await _Proto.edit(self, input_image, instructions, options=options)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _subscribe(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke ``fal_client.subscribe_async`` against the configured model.

        Isolated to a single private method so tests can ``patch.object``
        the call without reaching into the SDK module (same pattern Spec
        02 ``openai_compat`` uses for its ``_chat_*`` helpers). The
        ``fal_client`` import is local to this method so the module's
        top-level import graph stays SDK-free.

        The fal SDK reads its credential from the ``FAL_KEY`` environment
        variable. We set it for the duration of the call from
        :attr:`self._api_key` so callers don't have to manage the env
        var globally — this is the documented SDK-1.x convention.
        """
        import os

        import fal_client  # local import — SDK boundary

        prior_key = os.environ.get("FAL_KEY")
        os.environ["FAL_KEY"] = self._api_key
        try:
            result = await fal_client.subscribe_async(
                self._model,
                arguments=arguments,
                with_logs=False,
                client_timeout=self._timeout,
            )
        finally:
            if prior_key is None:
                os.environ.pop("FAL_KEY", None)
            else:
                os.environ["FAL_KEY"] = prior_key
        if not isinstance(result, dict):
            raise ImageProviderError(
                "fal returned a non-dict response",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "transient",
                    "response_type": type(result).__name__,
                },
            )
        return result

    async def _download_images(self, response: dict[str, Any]) -> list[GeneratedImage]:
        """Download each ``images[i].url`` to in-memory bytes via httpx.

        fal's CDN URLs expire; the backend persists bytes immediately so
        the service layer (T15) can land them in the workspace without
        re-fetching. Mirrors the Spec 13 ``ImageContent`` upload-bytes
        discipline (bytes are the canonical reference, the URL is
        ephemeral).
        """
        raw_images = response.get("images")
        if not isinstance(raw_images, list) or not raw_images:
            raise ImageProviderError(
                "fal returned no images",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "transient",
                },
            )

        results: list[GeneratedImage] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for raw in raw_images:
                if not isinstance(raw, dict):
                    raise ImageProviderError(
                        "fal image entry is not a dict",
                        context={
                            "provider": self._provider,
                            "model": self._model,
                            "reason": "transient",
                        },
                    )
                url = raw.get("url")
                width = raw.get("width")
                height = raw.get("height")
                content_type = raw.get("content_type", "image/png")
                if not isinstance(url, str) or not url:
                    raise ImageProviderError(
                        "fal image entry missing url",
                        context={
                            "provider": self._provider,
                            "model": self._model,
                            "reason": "transient",
                        },
                    )
                if not isinstance(width, int) or not isinstance(height, int):
                    raise ImageProviderError(
                        "fal image entry missing width/height",
                        context={
                            "provider": self._provider,
                            "model": self._model,
                            "reason": "transient",
                        },
                    )
                if not isinstance(content_type, str) or content_type not in _MEDIA_TYPES:
                    # Default to image/png when fal returns an unfamiliar
                    # content type — Flux 1.1 [pro] returns either png or
                    # jpeg; jpeg-default keeps the boundary type happy.
                    content_type = "image/png"

                http_response = await client.get(url)
                http_response.raise_for_status()
                body = http_response.content
                if not body:
                    raise ImageProviderError(
                        "fal CDN returned empty body",
                        context={
                            "provider": self._provider,
                            "model": self._model,
                            "reason": "transient",
                        },
                    )

                media_type: ImageMediaType = cast("ImageMediaType", content_type)
                results.append(
                    GeneratedImage(
                        image_bytes=body,
                        media_type=media_type,
                        width=width,
                        height=height,
                    )
                )

        return results

    def _reraise(self, exc: BaseException) -> NoReturn:
        """Translate any caught upstream exception into a domain exception.

        Mirrors the Spec 02 ``OpenAICompatibleBackend._reraise`` shape —
        one funnel; every adapter-boundary exception goes through here
        so callers depend on :class:`persona.imagegen.errors` types and
        never on ``fal_client.*`` or ``httpx.HTTPStatusError``.

        Returns:
            Never returns; declared :class:`typing.NoReturn` so callers
            can ``raise self._reraise(exc)``-style chain through the
            type-checker.
        """
        # Re-raise domain exceptions verbatim; the moderation /
        # unavailability paths above raise these directly.
        if isinstance(exc, (ImageGenUnavailableError, ImageProviderError, ContentRejectedError)):
            raise exc

        # httpx-level failures — distinguish auth (401/403) from rate
        # limit (429), input moderation (422), and transient (5xx).
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status in {401, 403}:
                raise ImageGenUnavailableError(
                    "fal rejected the API key",
                    context={
                        "provider": self._provider,
                        "model": self._model,
                        "reason": "auth",
                        "status": str(status),
                    },
                ) from exc
            if status == 429:
                retry_after = exc.response.headers.get("retry-after")
                ctx = {
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "rate_limit",
                }
                if retry_after is not None:
                    ctx["retry_after_s"] = str(retry_after)
                raise ImageProviderError("fal rate limited", context=ctx) from exc
            if status == 422:
                raise ContentRejectedError(
                    "fal rejected the prompt under content policy",
                    context={
                        "provider": self._provider,
                        "model": self._model,
                        "reason": "provider_moderation",
                        "stage": "input",
                    },
                ) from exc
            if 500 <= status < 600:
                raise ImageProviderError(
                    "fal upstream 5xx",
                    context={
                        "provider": self._provider,
                        "model": self._model,
                        "reason": "transient",
                        "status": str(status),
                    },
                ) from exc
            raise ImageProviderError(
                "fal upstream error",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "transient",
                    "status": str(status),
                },
            ) from exc

        # httpx timeout / network errors — transient.
        if isinstance(exc, httpx.TimeoutException):
            raise ImageProviderError(
                "fal request timed out",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "timeout",
                },
            ) from exc
        if isinstance(exc, httpx.HTTPError):
            raise ImageProviderError(
                "fal request failed",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "transient",
                },
            ) from exc

        # fal_client.* — match by class name to avoid pulling the SDK
        # into the type graph (the SDK module is intentionally lazy-
        # imported). The fal-client 1.x SDK exposes three error types:
        # FalClientError (base), FalClientHTTPError (carries the
        # upstream HTTP status), and FalClientTimeoutError. The HTTP
        # variant carries a ``status_code`` attribute (1.x convention);
        # we branch on it when present.
        exc_name = type(exc).__name__
        if exc_name == "FalClientTimeoutError":
            raise ImageProviderError(
                "fal request timed out",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "timeout",
                },
            ) from exc
        if exc_name == "FalClientHTTPError":
            status_attr = getattr(exc, "status_code", None)
            status = int(status_attr) if isinstance(status_attr, int) else 0
            if status in {401, 403}:
                raise ImageGenUnavailableError(
                    "fal rejected the API key",
                    context={
                        "provider": self._provider,
                        "model": self._model,
                        "reason": "auth",
                        "status": str(status),
                    },
                ) from exc
            if status == 429:
                raise ImageProviderError(
                    "fal rate limited",
                    context={
                        "provider": self._provider,
                        "model": self._model,
                        "reason": "rate_limit",
                    },
                ) from exc
            if status == 422:
                raise ContentRejectedError(
                    "fal rejected the prompt under content policy",
                    context={
                        "provider": self._provider,
                        "model": self._model,
                        "reason": "provider_moderation",
                        "stage": "input",
                    },
                ) from exc
            raise ImageProviderError(
                "fal upstream error",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "transient",
                    "status": str(status),
                },
            ) from exc
        if exc_name == "FalClientError":
            raise ImageProviderError(
                "fal request failed",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "reason": "transient",
                },
            ) from exc

        # Last-resort transient.
        raise ImageProviderError(
            "fal request failed",
            context={
                "provider": self._provider,
                "model": self._model,
                "reason": "transient",
                "exc_type": exc_name,
            },
        ) from exc
