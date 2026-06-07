"""OpenAI image-generation backend (Spec 15 T06).

Concrete :class:`persona.imagegen.protocol.ImageBackend` for the OpenAI
``gpt-image-1`` family (and successors via
:attr:`persona.imagegen.config.ImageBackendConfig.model`). Mirrors
:class:`persona.backends.openai_compat.OpenAICompatibleBackend` per the
Spec 15 decisions gate paragraph #1 (Spec 02 mirror discipline): the
per-model capability matrix lives co-located near the top of this file
(mirror of ``openai_compat.py:72-121``), credentials fail fast at
construction, provider SDK exceptions are caught at the adapter boundary
and re-raised as :mod:`persona.imagegen.errors` domain types so callers
depend on our types — not on ``openai``.

The backend NEVER writes bytes to disk. It returns the raw decoded image
bytes in :attr:`persona.imagegen.result.GeneratedImage.image_bytes`; the
hosted service layer (Spec 15 T15) persists the bytes to the per-persona
workspace via ``resolve_sandbox_path`` and rewrites the result so
``workspace_path`` is populated and ``image_bytes`` is zeroed for the
response envelope.

References:
    docs/specs/phase2/spec_15/decisions.md gate paragraph #1 +
    D-15-1 + D-15-X-pydantic-boundary-types + D-15-X-size-rounding +
    D-15-X-demo-primary-provider; research.md §2.1.
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, Any, Literal

import openai

from persona.imagegen.config import DEFAULT_BASE_URLS
from persona.imagegen.errors import (
    ContentRejectedError,
    ImageGenUnavailableError,
    ImageProviderError,
)
from persona.imagegen.protocol import ImageBackend as _ImageBackendProtocol
from persona.imagegen.result import (
    GeneratedImage,
    GenerationResult,
    ImageGenOptions,
    ImageMediaType,
)
from persona.logging import get_logger

if TYPE_CHECKING:
    from persona.imagegen.config import ImageBackendConfig

__all__ = ["OpenAIImageBackend"]


_LOG = get_logger("imagegen.openai")


# Per-model size-capability matrix (D-15-1 + D-15-X-size-rounding).
# Mirror of ``persona.backends.openai_compat._NATIVE_TOOLS_CAPABILITY``
# at lines 72-121: a dict keyed by model id, valued by ``"all"`` or a
# closed ``frozenset`` of supported size strings. The matrix is
# extensible — adding a successor like ``gpt-image-1.5`` is a single
# entry plus an optional D-15-X-size-rounding entry below.
_OPENAI_IMAGE_CAPABILITY: dict[str, frozenset[str] | Literal["all"]] = {
    "gpt-image-1": frozenset({"1024x1024", "1024x1536", "1536x1024"}),
}


# Neutral-size → OpenAI-supported-size rounding (D-15-X-size-rounding).
# The neutral ``ImageGenOptions.size`` Literal carries the values
# ``1024x1024`` / ``1024x1792`` / ``1792x1024`` per D-15-3; OpenAI
# accepts ``1024x1024`` / ``1024x1536`` / ``1536x1024`` so the
# non-square neutral presets round to their nearest OpenAI sibling at
# the wire. The audit log captures the REQUESTED size (the service
# layer in T15 owns that record) so the operator can see provider-
# specific lossy mapping.
_OPENAI_SIZE_ROUNDING: dict[str, str] = {
    "1024x1792": "1024x1536",
    "1792x1024": "1536x1024",
}


# Neutral-quality → OpenAI-supported-quality mapping (D-15-3 + research §2.3).
# ``standard`` → ``medium`` keeps the cost/quality midpoint on OpenAI's
# three-tier ladder (low / medium / high); ``high`` is pass-through.
_OPENAI_QUALITY_MAPPING: dict[str, str] = {
    "standard": "medium",
    "high": "high",
}


def _image_size_supported(model: str, size: str) -> bool:
    """Look up the size capability for an OpenAI ``(model, size)`` pair.

    Mirrors :func:`persona.backends.openai_compat._native_tools_supported`.
    Returns ``True`` iff the rounded (or pass-through) size is in the
    model's supported set. Unlisted models fall back to ``frozenset()``
    so an unknown model fails closed with
    ``ImageProviderError(reason="unsupported_option")``.
    """
    capability = _OPENAI_IMAGE_CAPABILITY.get(model, frozenset())
    if capability == "all":
        return True
    assert isinstance(capability, frozenset)
    return size in capability


def _media_type_for_format(output_format: str) -> ImageMediaType:
    """Map OpenAI ``output_format`` to the neutral IANA media type."""
    if output_format == "png":
        return "image/png"
    if output_format == "jpeg":
        return "image/jpeg"
    if output_format == "webp":
        return "image/webp"
    # Defensive fallback — OpenAI's enum is closed but new variants may
    # land. Treat unknown as png (the OpenAI default) and log.
    _LOG.warning("unknown output_format from openai", output_format=output_format)
    return "image/png"


def _parse_size(size: str) -> tuple[int, int]:
    """Parse a ``"WxH"`` size string into ``(width, height)``."""
    width_str, _, height_str = size.partition("x")
    return int(width_str), int(height_str)


def _is_moderation_blocked(exc: openai.BadRequestError) -> bool:
    """Detect OpenAI's moderation rejection shape.

    The OpenAI ``BadRequestError`` carries the moderation rationale in
    ``exc.code == "moderation_blocked"`` when the SDK parsed the body;
    older releases / proxies surface it via ``exc.body["error"]["code"]``.
    Both paths are checked so the moderation rejection lands as
    :class:`ContentRejectedError` regardless of SDK version.

    References:
        research.md §2.1 + https://help.apiyi.com/en/fix-gpt-image-2-
        moderation-blocked-400-error-en.html.
    """
    if getattr(exc, "code", None) == "moderation_blocked":
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("code") == "moderation_blocked":
            return True
    # Defensive substring check — some SDK/proxy combinations only render
    # the rationale in the message string. Conservative: a literal
    # ``"moderation_blocked"`` substring is unambiguous.
    return "moderation_blocked" in str(exc)


def _moderation_stage(exc: openai.BadRequestError) -> str:
    """Disambiguate input vs output moderation stage from the error message.

    OpenAI's message hints at the stage: "generated image" / "output"
    refer to post-generation rejection; everything else is treated as
    input-stage. The discriminator lives in
    :attr:`ContentRejectedError.context["stage"]` per
    :mod:`persona.imagegen.errors` documentation.
    """
    message = str(exc).lower()
    if "generated image" in message or "output" in message:
        return "output"
    return "input"


class OpenAIImageBackend:
    """Async image-generation backend for OpenAI ``gpt-image-1.x``.

    Implements :class:`persona.imagegen.protocol.ImageBackend`. Construction
    fails fast (:class:`ImageGenUnavailableError`) when the API key is
    missing or empty — mirroring the Spec 02
    :class:`persona.backends.openai_compat.OpenAICompatibleBackend`
    ``AuthenticationError`` at-construction discipline (D-02-13).
    """

    def __init__(self, config: ImageBackendConfig) -> None:
        """Construct + validate the backend.

        Args:
            config: Image backend configuration. ``config.provider`` MUST
                be ``"openai"`` (the factory enforces this); ``config.model``
                must be a key in :data:`_OPENAI_IMAGE_CAPABILITY` for size
                capability lookup (an unknown model is allowed at
                construction time; per-call ``size`` checks then fail
                closed with :class:`ImageProviderError`).

        Raises:
            ImageGenUnavailableError: ``config.api_key`` is ``None`` or
                empty — the env var was unset. Fail-fast per D-02-13.
        """
        if config.api_key is None or not config.api_key.get_secret_value():
            raise ImageGenUnavailableError(
                "missing OpenAI API key",
                context={"provider": "openai"},
            )

        self._config = config
        self._model = config.model
        self._timeout = config.request_timeout_s
        self._base_url = config.base_url or DEFAULT_BASE_URLS.get("openai")
        self._client = openai.AsyncOpenAI(
            api_key=config.api_key.get_secret_value(),
            base_url=self._base_url,
            timeout=self._timeout,
        )

        _LOG.debug(
            "constructed",
            provider="openai",
            model=self._model,
            base_url=self._base_url,
            timeout_s=self._timeout,
        )

    @property
    def provider_name(self) -> str:
        """Return the stable provider identifier ``"openai"``."""
        return "openai"

    @property
    def model_name(self) -> str:
        """Return the configured OpenAI model identifier (e.g. ``gpt-image-1``)."""
        return self._model

    async def generate(
        self,
        prompt: str,
        *,
        options: ImageGenOptions | None = None,
    ) -> GenerationResult:
        """Single-shot image generation against OpenAI.

        Args:
            prompt: The (already merged with ``visual_style`` by T11)
                text description of the image.
            options: Neutral generation knobs. ``None`` means use a
                default :class:`ImageGenOptions`.

        Returns:
            :class:`GenerationResult` carrying ``len(images) == options.count``
            :class:`GeneratedImage` instances with raw bytes populated and
            ``workspace_path`` ``None`` (the service layer in T15 owns
            disk-write + ``workspace_path`` rewrite).

        Raises:
            ImageGenUnavailableError: provider returned 401 / 403.
            ImageProviderError: rate limit (``reason="rate_limit"``),
                timeout (``reason="timeout"``), model-not-found
                (``reason="model_not_found"``), unsupported ``(model, size)``
                pair (``reason="unsupported_option"``), or transient 5xx
                (``reason="transient"``). The discriminator is
                ``context["reason"]``.
            ContentRejectedError: OpenAI moderation rejected the prompt
                (input stage) or the generated image (output stage).
                ``context["reason"] = "provider_moderation"`` and
                ``context["stage"]`` carries ``"input"`` / ``"output"``.
        """
        opts = options if options is not None else ImageGenOptions()
        wire_size = _OPENAI_SIZE_ROUNDING.get(opts.size, opts.size)
        wire_quality = _OPENAI_QUALITY_MAPPING.get(opts.quality, opts.quality)

        if not _image_size_supported(self._model, wire_size):
            # Fail closed — an unlisted model or a future neutral size
            # that does not map cleanly raises a structured error rather
            # than letting the SDK 400 with a less-specific message.
            raise ImageProviderError(
                f"size {opts.size!r} (wire {wire_size!r}) is not supported by "
                f"model {self._model!r}",
                context={
                    "provider": "openai",
                    "model": self._model,
                    "reason": "unsupported_option",
                    "size": opts.size,
                    "wire_size": wire_size,
                },
            )

        kwargs: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "size": wire_size,
            "quality": wire_quality,
            "n": opts.count,
        }

        started = time.perf_counter()
        try:
            response = await self._client.images.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001 — adapter boundary; classified below
            self._reraise(exc)

        latency_ms = (time.perf_counter() - started) * 1000.0
        return self._parse_response(response, wire_size, latency_ms)

    async def edit(
        self,
        input_image: GeneratedImage,
        instructions: str,
        *,
        options: ImageGenOptions | None = None,
    ) -> GenerationResult:
        """Reserved for v1.x — delegates to the Protocol default.

        Per D-15-X-edit-protocol-reservation, v1 concrete backends do NOT
        override ``edit``; calling it raises :class:`NotImplementedError`
        via the Protocol default. The method is declared here so the
        :class:`persona.imagegen.protocol.ImageBackend` Protocol's
        runtime-checkable :func:`isinstance` recognises this class as a
        conforming implementation.
        """
        return await _ImageBackendProtocol.edit(self, input_image, instructions, options=options)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        response: Any,  # noqa: ANN401 — SDK return type
        wire_size: str,
        latency_ms: float,
    ) -> GenerationResult:
        """Unpack OpenAI's ``images.generate`` response into a :class:`GenerationResult`.

        OpenAI ``gpt-image-1`` always returns ``b64_json`` (no URL form)
        per research §2.1. The ``revised_prompt`` field is preserved on
        each :class:`GeneratedImage` so the audit log can record what the
        provider actually generated against. Width / height are echoed
        from the wire size (OpenAI does not populate per-image dims in
        the response shape).

        Args:
            response: The raw SDK response object.
            wire_size: The wire-level size string (post-rounding).
            latency_ms: Wall-clock latency in milliseconds.

        Returns:
            :class:`GenerationResult` with one image per response entry.
        """
        data = getattr(response, "data", None) or []
        if not data:
            raise ImageProviderError(
                "openai returned an empty data array",
                context={
                    "provider": "openai",
                    "model": self._model,
                    "reason": "transient",
                },
            )

        width, height = _parse_size(wire_size)
        media_type: ImageMediaType = "image/png"  # OpenAI default per research §2.1
        images: list[GeneratedImage] = []
        for entry in data:
            b64 = getattr(entry, "b64_json", None) or ""
            if not b64:
                # The SDK returned an item without ``b64_json``; treat as
                # transient — never silently empty (Dominant Concern #2:
                # fail-loud).
                raise ImageProviderError(
                    "openai response entry missing b64_json",
                    context={
                        "provider": "openai",
                        "model": self._model,
                        "reason": "transient",
                    },
                )
            try:
                image_bytes = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError) as exc:
                raise ImageProviderError(
                    "openai returned malformed base64 image data",
                    context={
                        "provider": "openai",
                        "model": self._model,
                        "reason": "transient",
                    },
                ) from exc
            revised_prompt = getattr(entry, "revised_prompt", None)
            images.append(
                GeneratedImage(
                    image_bytes=image_bytes,
                    workspace_path=None,
                    media_type=media_type,
                    width=width,
                    height=height,
                    revised_prompt=revised_prompt if isinstance(revised_prompt, str) else None,
                )
            )

        return GenerationResult(
            images=images,
            provider="openai",
            model=self._model,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Error mapping
    # ------------------------------------------------------------------

    def _reraise(self, exc: BaseException) -> Any:  # noqa: ANN401 — re-raises
        """Map an ``openai`` SDK exception to a :mod:`persona.imagegen.errors` domain type.

        Mirrors :meth:`persona.backends.openai_compat.OpenAICompatibleBackend._reraise`.
        The ``moderation_blocked`` ``BadRequestError`` variant lands as
        :class:`ContentRejectedError` (not :class:`ImageProviderError`) so
        callers branching on safety vs transient failure stay
        disambiguated (Spec 15 §6 layer 1).
        """
        provider = "openai"
        model = self._model

        if isinstance(exc, openai.AuthenticationError):
            raise ImageGenUnavailableError(
                str(exc),
                context={"provider": provider},
            ) from exc

        if isinstance(exc, openai.RateLimitError):
            retry_after = _extract_retry_after_s(
                getattr(getattr(exc, "response", None), "headers", None)
            )
            ctx: dict[str, str] = {"provider": provider, "reason": "rate_limit"}
            if retry_after is not None:
                ctx["retry_after_s"] = retry_after
            raise ImageProviderError(str(exc), context=ctx) from exc

        if isinstance(exc, openai.NotFoundError):
            raise ImageProviderError(
                str(exc),
                context={
                    "provider": provider,
                    "model": model,
                    "reason": "model_not_found",
                },
            ) from exc

        if isinstance(exc, openai.BadRequestError):
            if _is_moderation_blocked(exc):
                stage = _moderation_stage(exc)
                raise ContentRejectedError(
                    str(exc),
                    context={
                        "provider": provider,
                        "reason": "provider_moderation",
                        "stage": stage,
                    },
                ) from exc
            raise ImageProviderError(
                str(exc),
                context={
                    "provider": provider,
                    "model": model,
                    "reason": "bad_request",
                },
            ) from exc

        if isinstance(exc, openai.APITimeoutError | openai.APIConnectionError):
            raise ImageProviderError(
                str(exc),
                context={"provider": provider, "reason": "timeout"},
            ) from exc

        # Anything else — unmapped SDK errors land as transient so the
        # caller can decide whether to retry. ``underlying`` carries the
        # original type name for observability.
        raise ImageProviderError(
            str(exc),
            context={
                "provider": provider,
                "model": model,
                "reason": "transient",
                "underlying": type(exc).__name__,
            },
        ) from exc


def _extract_retry_after_s(headers: Any) -> str | None:  # noqa: ANN401 — SDK type
    """Return ``retry-after`` header value as a string, or ``None``.

    Mirror of :func:`persona.backends.openai_compat._extract_retry_after_s`
    (D-02-8): the header is the only source — we never invent a default.
    """
    if headers is None:
        return None
    try:
        value = headers.get("retry-after") if hasattr(headers, "get") else None
    except (AttributeError, TypeError):
        return None
    if value is None:
        return None
    return str(value)
