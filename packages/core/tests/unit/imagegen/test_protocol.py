"""Tests for ``persona.imagegen.protocol`` — the ``ImageBackend`` Protocol (Spec 15 T04).

Mirrors ``tests/unit/backends/test_backends_protocol.py`` per the Spec 15
decisions gate paragraph #1 ("Mirror Spec 02 verbatim"). Asserts:

* Protocol membership via :func:`isinstance` against ``@runtime_checkable``.
* Required-attribute coverage (``provider_name``, ``model_name``,
  ``generate``, ``edit``).
* The :meth:`edit` Protocol default raises :class:`NotImplementedError`
  per D-15-X-edit-protocol-reservation; v1 backends do NOT override.
"""

from __future__ import annotations

import time

import pytest
from persona.imagegen import (
    GeneratedImage,
    GenerationResult,
    ImageBackend,
    ImageGenOptions,
)
from persona.imagegen.protocol import ImageBackend as ImageBackendCls


class _GoodBackend:
    """Minimal valid ``ImageBackend`` impl. Used to assert isinstance."""

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "test-model"

    async def generate(
        self,
        prompt: str,  # noqa: ARG002
        *,
        options: ImageGenOptions | None = None,  # noqa: ARG002
    ) -> GenerationResult:
        return GenerationResult(
            images=[
                GeneratedImage(
                    image_bytes=b"\x89PNG",
                    media_type="image/png",
                    width=1024,
                    height=1024,
                )
            ],
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=0.0,
        )

    async def edit(
        self,
        input_image: GeneratedImage,  # noqa: ARG002
        instructions: str,  # noqa: ARG002
        *,
        options: ImageGenOptions | None = None,  # noqa: ARG002
    ) -> GenerationResult:
        # v1 backends do NOT override; delegate to the Protocol default.
        return await ImageBackendCls.edit(self, input_image, instructions, options=options)


class _MissingGenerateBackend:
    """Lacks ``generate``. Should fail the isinstance check."""

    @property
    def provider_name(self) -> str:
        return "x"

    @property
    def model_name(self) -> str:
        return "y"


class _MissingProviderNameBackend:
    """Lacks ``provider_name``. Should fail the isinstance check."""

    @property
    def model_name(self) -> str:
        return "test-model"

    async def generate(
        self,
        prompt: str,  # noqa: ARG002
        *,
        options: ImageGenOptions | None = None,  # noqa: ARG002
    ) -> GenerationResult:
        raise NotImplementedError


class TestProtocolMembership:
    def test_good_backend_is_image_backend(self) -> None:
        assert isinstance(_GoodBackend(), ImageBackend)

    def test_missing_generate_is_not_image_backend(self) -> None:
        assert not isinstance(_MissingGenerateBackend(), ImageBackend)

    def test_missing_provider_name_is_not_image_backend(self) -> None:
        assert not isinstance(_MissingProviderNameBackend(), ImageBackend)

    def test_runtime_checkable(self) -> None:
        # Per the Spec 02 mirror discipline; required so isinstance()
        # works at the service-layer dispatch boundary.
        assert getattr(ImageBackend, "_is_runtime_protocol", False)


class TestProperties:
    def test_required_properties(self) -> None:
        backend = _GoodBackend()
        assert backend.provider_name == "test"
        assert backend.model_name == "test-model"

    @pytest.mark.parametrize(
        "provider_name",
        ["openai", "fal"],
    )
    def test_provider_name_is_lowercase_ascii(self, provider_name: str) -> None:
        # Convention lock — provider_name doc string says
        # "Lowercase, ASCII. Stable across releases."
        assert provider_name.isascii()
        assert provider_name == provider_name.lower()


class TestGenerate:
    @pytest.mark.asyncio
    async def test_generate_returns_generation_result(self) -> None:
        backend = _GoodBackend()
        result = await backend.generate("a red bicycle")
        assert isinstance(result, GenerationResult)
        assert result.provider == "test"
        assert result.model == "test-model"
        assert len(result.images) == 1
        assert result.images[0].media_type == "image/png"

    @pytest.mark.asyncio
    async def test_generate_accepts_explicit_options(self) -> None:
        backend = _GoodBackend()
        options = ImageGenOptions(size="1024x1792", count=2, quality="high")
        # Backend ignores options in the fake — what we're asserting is
        # the shape passes through type-checking + at runtime.
        result = await backend.generate("a cat", options=options)
        assert isinstance(result, GenerationResult)

    @pytest.mark.asyncio
    async def test_generate_default_options_none(self) -> None:
        # The Protocol allows options=None; backends default to a fresh
        # ImageGenOptions() internally.
        backend = _GoodBackend()
        result = await backend.generate("a cat", options=None)
        assert isinstance(result, GenerationResult)

    @pytest.mark.asyncio
    async def test_generate_is_async(self) -> None:
        # Sanity — generate is a coroutine, not sync. Mirrors the
        # ChatBackend.chat Spec 02 invariant.
        backend = _GoodBackend()
        coro = backend.generate("a cat")
        try:
            assert hasattr(coro, "__await__")
        finally:
            await coro


class TestEditReservation:
    """D-15-X-edit-protocol-reservation invariants."""

    @pytest.mark.asyncio
    async def test_edit_default_raises_not_implemented(self) -> None:
        backend = _GoodBackend()
        img = GeneratedImage(
            image_bytes=b"data",
            media_type="image/png",
            width=1024,
            height=1024,
        )
        with pytest.raises(NotImplementedError, match="edit not supported in v1"):
            await backend.edit(img, "make it warmer")

    @pytest.mark.asyncio
    async def test_edit_signature_accepts_options(self) -> None:
        # The reservation specifies an ``options`` kwarg so a v1.x
        # override slots in without redesign. Passing options through the
        # default still raises NotImplementedError but proves the
        # signature compiles.
        backend = _GoodBackend()
        img = GeneratedImage(
            image_bytes=b"data",
            media_type="image/png",
            width=1024,
            height=1024,
        )
        options = ImageGenOptions(size="1024x1024", count=1, quality="standard")
        with pytest.raises(NotImplementedError):
            await backend.edit(img, "make it warmer", options=options)


class TestReExports:
    def test_image_backend_importable_from_package(self) -> None:
        from persona.imagegen import ImageBackend as _ImageBackend

        assert _ImageBackend is ImageBackend


class TestLatencyShape:
    """Sanity for fakes that need to measure latency_ms client-side."""

    @pytest.mark.asyncio
    async def test_latency_ms_is_float(self) -> None:
        backend = _GoodBackend()
        t0 = time.perf_counter()
        result = await backend.generate("a cat")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert isinstance(result.latency_ms, float)
        # Sanity: the fake returns 0.0; real backends would record elapsed.
        assert result.latency_ms >= 0.0
        assert elapsed_ms >= 0.0
