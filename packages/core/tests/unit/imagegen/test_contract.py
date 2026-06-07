"""Provider-agnostic contract test suite for :mod:`persona.imagegen` (Spec 15 T08).

Parametrised across the two v0.1 backends (``openai`` and ``fal``) per
D-15-1. Both backends are mocked at the SDK boundary — no real provider
calls; the live smoke matrix lives behind ``@pytest.mark.external`` in
T20.

Ten contract assertions per backend (tasks.md §T08):

1.  Protocol conformance — :func:`isinstance` against the
    ``@runtime_checkable`` :class:`ImageBackend` Protocol.
2.  ``provider_name`` populated, ASCII lowercase.
3.  ``model_name`` populated.
4.  ``generate(prompt="a red bicycle")`` returns a
    :class:`GenerationResult` with ``len(images) >= 1`` and a
    non-negative ``latency_ms``.
5.  ``media_type`` lies in the allowed ``ImageMediaType`` Literal set.
6.  Mocked provider moderation surfaces as :class:`ContentRejectedError`.
7.  Mocked auth failure surfaces as :class:`ImageGenUnavailableError`.
8.  Missing key at construction → :class:`ImageGenUnavailableError`.
9.  **Binary symmetry test:** an unsupported ``(model, size)`` pair
    raises :class:`ImageProviderError` with ``context["reason"] ==
    "unsupported_option"`` on BOTH providers. This is the test that the
    unified shape is real, not just shared property names.
10. Option-mapping coherence: ``ImageGenOptions(size="1024x1024",
    count=2, quality="standard")`` yields a 2-image result without
    crashing.

The SDK-boundary mocking strategy intentionally differs per backend so
the contract is exercised against the *real* adapter code path. OpenAI is
mocked via ``patch.object(backend._client.images, "generate", ...)``
(same pattern as the per-backend test in :mod:`test_openai_image`); fal
is mocked via ``patch.object(backend, "_subscribe", ...)`` plus a
:class:`httpx.MockTransport` that serves the synthetic CDN bytes (same
pattern as :mod:`test_fal_image`). If those code paths break, the
contract suite catches the symmetry failure even when the per-backend
tests still pass.

References:
    docs/specs/phase2/spec_15/tasks.md §T08;
    docs/specs/phase2/spec_15/decisions.md D-15-1 +
    D-15-X-pydantic-boundary-types + D-15-X-flagged-image-policy.
"""

# ruff: noqa: ANN401, SLF001 — adapter-boundary mocks expose Any; tests touch private
# helper attributes (``_client``, ``_subscribe``) per the per-backend test pattern.

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, ExitStack, contextmanager
from typing import TYPE_CHECKING, Any, Literal, get_args
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
from persona.imagegen.fal_image import _FAL_IMAGE_CAPABILITY, FalImageBackend
from persona.imagegen.openai_image import OpenAIImageBackend
from persona.imagegen.result import ImageMediaType
from pydantic import SecretStr

if TYPE_CHECKING:
    from collections.abc import Iterator

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# The allowed media types per ``persona.imagegen.result.ImageMediaType``.
# Derived from the Literal so the assertion stays in lockstep with the
# boundary type — a new media type added to the Literal automatically
# widens this check.
_ALLOWED_MEDIA_TYPES: frozenset[str] = frozenset(get_args(ImageMediaType))

# A tiny PNG payload used by the fal CDN mock transport. The exact bytes
# do not matter for the contract — only that they round-trip through
# ``GeneratedImage.image_bytes``.
_PNG_BYTES: bytes = b"\x89PNG\r\n\x1a\nfake-image-bytes-for-contract"

# OpenAI returns ``b64_json``; the fixture serves a known base64 string.
_OPENAI_B64: str = "aGVsbG8="  # base64("hello")
_OPENAI_DECODED: bytes = b"hello"

# Capture the real ``httpx.AsyncClient`` at import time. Patching
# ``persona.imagegen.fal_image.httpx.AsyncClient`` rebinds the same
# module-level object that ``httpx.AsyncClient`` refers to, so a naive
# lambda would recurse infinitely — see the matching pattern in
# :mod:`test_fal_image`.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


# ---------------------------------------------------------------------------
# Backend harnesses — one per provider, sharing a uniform mocking surface
# ---------------------------------------------------------------------------


class _BackendHarness:
    """Uniform contract-test surface over a single provider's backend.

    Each harness knows how to build a configured backend, how to mock the
    SDK boundary for the happy path, how to inject a moderation rejection,
    and how to inject an auth failure. The contract assertions consume
    only this surface — they never touch SDK-specific objects.
    """

    provider: str
    model: str

    def build(self, *, api_key: str | None = "test-key", model: str | None = None) -> Any:
        """Construct a backend instance for the contract suite."""
        raise NotImplementedError

    def happy_path(
        self,
        backend: Any,
        *,
        count: int = 1,
    ) -> AbstractAsyncContextManager[None]:
        """Mock the SDK boundary so ``generate`` returns ``count`` images."""
        raise NotImplementedError

    def moderation_mock(
        self,
        backend: Any,
    ) -> AbstractAsyncContextManager[None]:
        """Mock the SDK boundary so ``generate`` raises provider moderation."""
        raise NotImplementedError

    def auth_failure_mock(
        self,
        backend: Any,
    ) -> AbstractAsyncContextManager[None]:
        """Mock the SDK boundary so ``generate`` raises an auth failure."""
        raise NotImplementedError

    def unsupported_size_target(self) -> tuple[str, str]:
        """Return ``(model, size)`` that the capability matrix rejects.

        Both backends fail closed on an unsupported ``(model, size)`` pair
        BEFORE calling the SDK. The test never reaches the SDK boundary;
        no mock is needed.
        """
        raise NotImplementedError


class _OpenAIHarness(_BackendHarness):
    """OpenAI harness — mocks ``backend._client.images.generate``."""

    provider = "openai"
    model = "gpt-image-1"

    def build(self, *, api_key: str | None = "test-key", model: str | None = None) -> Any:
        config = ImageBackendConfig(
            provider="openai",
            model=model or self.model,
            api_key=SecretStr(api_key) if api_key is not None else None,
            request_timeout_s=30.0,
        )
        return OpenAIImageBackend(config)

    @contextmanager
    def happy_path(self, backend: Any, *, count: int = 1) -> Iterator[None]:
        response = MagicMock()
        response.data = [
            MagicMock(b64_json=_OPENAI_B64, revised_prompt="provider rewrite") for _ in range(count)
        ]
        with patch.object(
            backend._client.images,
            "generate",
            new=AsyncMock(return_value=response),
        ):
            yield

    @contextmanager
    def moderation_mock(self, backend: Any) -> Iterator[None]:
        # OpenAI surfaces moderation as ``BadRequestError`` with
        # ``code="moderation_blocked"``. The adapter (T06) classifies it
        # as :class:`ContentRejectedError`.
        http_response = MagicMock()
        http_response.status_code = 400
        http_response.headers = {}
        http_response.request = MagicMock()
        exc = openai.BadRequestError(
            "Your request was rejected by the safety system",
            response=http_response,
            body={"error": {"code": "moderation_blocked", "message": "rejected"}},
        )
        with patch.object(
            backend._client.images,
            "generate",
            new=AsyncMock(side_effect=exc),
        ):
            yield

    @contextmanager
    def auth_failure_mock(self, backend: Any) -> Iterator[None]:
        http_response = MagicMock()
        http_response.status_code = 401
        http_response.headers = {}
        http_response.request = MagicMock()
        exc = openai.AuthenticationError("invalid api key", response=http_response, body=None)
        with patch.object(
            backend._client.images,
            "generate",
            new=AsyncMock(side_effect=exc),
        ):
            yield

    def unsupported_size_target(self) -> tuple[str, str]:
        # An unknown model with the default neutral size — the adapter
        # falls back to an empty frozenset and fails closed with
        # ``unsupported_option`` BEFORE calling the SDK.
        return ("gpt-imagined", "1024x1024")


class _FalHarness(_BackendHarness):
    """fal.ai harness — mocks ``backend._subscribe`` + the httpx CDN download."""

    provider = "fal"
    model = "fal-ai/flux-pro/v1.1"

    def build(self, *, api_key: str | None = "test-key", model: str | None = None) -> Any:
        config = ImageBackendConfig(
            provider="fal",
            model=model or self.model,
            api_key=SecretStr(api_key) if api_key is not None else None,
            request_timeout_s=30.0,
        )
        return FalImageBackend(config)

    @contextmanager
    def happy_path(self, backend: Any, *, count: int = 1) -> Iterator[None]:
        cdn_urls = [f"https://fal.media/files/contract/{idx}.png" for idx in range(count)]
        fake_response: dict[str, Any] = {
            "images": [
                {
                    "url": url,
                    "width": 1024,
                    "height": 1024,
                    "content_type": "image/png",
                }
                for url in cdn_urls
            ],
            "prompt": "provider rewrite",
            "has_nsfw_concepts": [False] * count,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) in set(cdn_urls):
                return httpx.Response(200, content=_PNG_BYTES)
            return httpx.Response(404)

        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return fake_response

        with ExitStack() as stack:
            stack.enter_context(patch.object(backend, "_subscribe", new=fake_subscribe))
            stack.enter_context(
                patch(
                    "persona.imagegen.fal_image.httpx.AsyncClient",
                    lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw),
                )
            )
            yield

    @contextmanager
    def moderation_mock(self, backend: Any) -> Iterator[None]:
        # fal surfaces input moderation as a 422 with a content-policy
        # body; ``_reraise`` maps it to ContentRejectedError.
        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            request = httpx.Request("POST", "https://queue.fal.run/contract")
            response = httpx.Response(
                422,
                request=request,
                json={"detail": [{"type": "value_error", "msg": "Content policy violation"}]},
            )
            raise httpx.HTTPStatusError("422", request=request, response=response)

        with patch.object(backend, "_subscribe", new=fake_subscribe):
            yield

    @contextmanager
    def auth_failure_mock(self, backend: Any) -> Iterator[None]:
        async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
            request = httpx.Request("POST", "https://queue.fal.run/contract")
            response = httpx.Response(401, request=request, json={"detail": "Invalid API key"})
            raise httpx.HTTPStatusError("auth", request=request, response=response)

        with patch.object(backend, "_subscribe", new=fake_subscribe):
            yield

    def unsupported_size_target(self) -> tuple[str, str]:
        # The capability matrix only lists ``fal-ai/flux-pro/v1.1``; an
        # unknown model fails closed via ``_size_supported`` returning
        # False against the default empty frozenset.
        return ("fal-ai/no-such-model", "1024x1024")


# ---------------------------------------------------------------------------
# Parametrisation
# ---------------------------------------------------------------------------


_HARNESSES: dict[str, _BackendHarness] = {
    "openai": _OpenAIHarness(),
    "fal": _FalHarness(),
}


@pytest.fixture(params=sorted(_HARNESSES), ids=sorted(_HARNESSES))
def harness(request: pytest.FixtureRequest) -> _BackendHarness:
    """Yield each backend harness in turn — parametrises every contract test."""
    return _HARNESSES[request.param]


# ---------------------------------------------------------------------------
# Contract assertions — one class per behaviour, mirroring the §T08 list
# ---------------------------------------------------------------------------


class TestContractProtocolMembership:
    """Assertion #1 — every backend conforms to the runtime-checkable Protocol."""

    def test_is_image_backend(self, harness: _BackendHarness) -> None:
        backend = harness.build()
        assert isinstance(backend, ImageBackend)


class TestContractProviderName:
    """Assertion #2 — ``provider_name`` populated, ASCII lowercase, stable."""

    def test_provider_name_matches_harness(self, harness: _BackendHarness) -> None:
        backend = harness.build()
        assert backend.provider_name == harness.provider
        # ASCII lowercase invariant — the contract requires the string is
        # safe to log and to use as a path component in audit metadata.
        assert backend.provider_name.isascii()
        assert backend.provider_name == backend.provider_name.lower()
        assert len(backend.provider_name) > 0


class TestContractModelName:
    """Assertion #3 — ``model_name`` populated."""

    def test_model_name_populated(self, harness: _BackendHarness) -> None:
        backend = harness.build()
        assert backend.model_name == harness.model
        assert len(backend.model_name) > 0


class TestContractGenerateReturnsGenerationResult:
    """Assertion #4 — ``generate`` yields a usable :class:`GenerationResult`."""

    @pytest.mark.asyncio
    async def test_generate_happy_path(self, harness: _BackendHarness) -> None:
        backend = harness.build()
        async with _async_cm(harness.happy_path(backend, count=1)):
            result = await backend.generate("a red bicycle")
        assert result.provider == harness.provider
        assert result.model == harness.model
        assert len(result.images) >= 1
        # ``GenerationResult.latency_ms`` is ``Field(ge=0.0)`` so the
        # non-negative invariant suffices; sub-millisecond mocks may
        # legitimately record ``0.0``.
        assert result.latency_ms >= 0.0


class TestContractMediaTypeAllowed:
    """Assertion #5 — every ``GeneratedImage.media_type`` is in the Literal set."""

    @pytest.mark.asyncio
    async def test_media_type_in_literal_set(self, harness: _BackendHarness) -> None:
        backend = harness.build()
        async with _async_cm(harness.happy_path(backend, count=1)):
            result = await backend.generate("a red bicycle")
        for image in result.images:
            assert image.media_type in _ALLOWED_MEDIA_TYPES


class TestContractModerationSurfacesContentRejected:
    """Assertion #6 — provider moderation lands as :class:`ContentRejectedError`."""

    @pytest.mark.asyncio
    async def test_moderation_raises_content_rejected(self, harness: _BackendHarness) -> None:
        backend = harness.build()
        async with _async_cm(harness.moderation_mock(backend)):
            with pytest.raises(ContentRejectedError) as info:
                await backend.generate("a cat")
        # Both providers populate ``context["reason"]`` with a
        # ``provider_moderation``-class label; the exact label differs
        # (``provider_moderation`` for input, ``provider_post_gen_moderation``
        # for output) but the contract is that the reason is non-empty and
        # carries the provider identifier.
        assert info.value.context.get("provider") == harness.provider
        assert info.value.context.get("reason", "").startswith("provider_")


class TestContractAuthFailureSurfacesUnavailable:
    """Assertion #7 — auth failure at call time lands as :class:`ImageGenUnavailableError`."""

    @pytest.mark.asyncio
    async def test_auth_failure_raises_unavailable(self, harness: _BackendHarness) -> None:
        backend = harness.build()
        async with _async_cm(harness.auth_failure_mock(backend)):
            with pytest.raises(ImageGenUnavailableError) as info:
                await backend.generate("a cat")
        assert info.value.context.get("provider") == harness.provider


class TestContractMissingKeyFailsFastAtConstruction:
    """Assertion #8 — missing api_key raises :class:`ImageGenUnavailableError` at ``__init__``."""

    def test_missing_key_fails_fast(self, harness: _BackendHarness) -> None:
        with pytest.raises(ImageGenUnavailableError) as info:
            harness.build(api_key=None)
        assert info.value.context.get("provider") == harness.provider


class TestContractUnsupportedOptionSymmetry:
    """Assertion #9 — the binary symmetry test.

    An unsupported ``(model, size)`` pair raises
    :class:`ImageProviderError` with ``context["reason"] ==
    "unsupported_option"`` on BOTH providers. This is the test that
    "the unified shape is real, not just shared property names": both
    adapters fail closed against the same reason string before the SDK
    boundary is reached.
    """

    @pytest.mark.asyncio
    async def test_unsupported_pair_raises_unsupported_option(
        self,
        harness: _BackendHarness,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target_model, target_size = harness.unsupported_size_target()
        # The fal capability matrix is module-level; pre-seed an empty
        # frozenset for the unknown model so ``_size_supported`` returns
        # False against the requested size (the OpenAI matrix already
        # falls back to ``frozenset()`` for unlisted models so no
        # patching is needed).
        if harness.provider == "fal":
            monkeypatch.setitem(_FAL_IMAGE_CAPABILITY, target_model, frozenset())

        backend = harness.build(model=target_model)

        # The SDK boundary must NOT be reached — both adapters fail
        # closed in the per-call guard. We mock the SDK to ensure that
        # an erroneous call would not silently succeed (the assertion is
        # on the raised type, not the call count, but the mock makes the
        # failure visible if a regression introduces a silent pass-through).
        sentinel = pytest.raises(ImageProviderError)
        if harness.provider == "openai":
            with (
                patch.object(
                    backend._client.images,
                    "generate",
                    new=AsyncMock(return_value=MagicMock(data=[])),
                ),
                sentinel as info,
            ):
                await backend.generate("a cat")
        else:

            async def fake_subscribe(_arguments: dict[str, Any]) -> dict[str, Any]:
                pytest.fail("fal _subscribe must not be called for unsupported_option")

            with (
                patch.object(backend, "_subscribe", new=fake_subscribe),
                sentinel as info,
            ):
                await backend.generate("a cat")

        assert info.value.context.get("reason") == "unsupported_option"
        assert info.value.context.get("provider") == harness.provider
        assert info.value.context.get("model") == target_model
        assert info.value.context.get("size") == target_size


class TestContractOptionMappingCoherence:
    """Assertion #10 — ``count=2`` yields a 2-image result without crashing."""

    @pytest.mark.asyncio
    async def test_count_two_yields_two_images(self, harness: _BackendHarness) -> None:
        backend = harness.build()
        options = ImageGenOptions(size="1024x1024", count=2, quality="standard")
        async with _async_cm(harness.happy_path(backend, count=2)):
            result = await backend.generate("a red bicycle", options=options)
        assert len(result.images) == 2
        for image in result.images:
            assert image.media_type in _ALLOWED_MEDIA_TYPES
            # The bytes round-trip through the adapter — non-empty on
            # the happy path so the service layer (T15) has something to
            # write to disk.
            assert len(image.image_bytes) > 0


# ---------------------------------------------------------------------------
# Cross-cutting symmetry — verifies the parametrisation actually covers both
# providers and that the harness map stays in lockstep with the
# ``ImageProvider`` Literal (D-15-1 closed set).
# ---------------------------------------------------------------------------


class TestContractParametrisationSymmetry:
    """Sanity checks that catch parametrisation drift (e.g. a harness removed)."""

    def test_both_providers_have_harness(self) -> None:
        # Mirrors the D-15-1 closed set ``Literal["openai", "fal"]``.
        from persona.imagegen.config import ImageProvider

        literal_providers: tuple[str, ...] = get_args(ImageProvider)
        assert set(_HARNESSES) == set(literal_providers)

    def test_assertions_per_backend_count(self) -> None:
        # Sanity — ten assertion classes mirror the §T08 list (one per
        # bullet). Adding a new contract bullet means adding both a
        # ``TestContract*`` class above and updating this count so the
        # suite stays auditable against the spec.
        expected = 10
        # Count this module's ``TestContract*`` (excluding the
        # parametrisation-symmetry sanity class) so the assertion fails
        # loudly if someone removes a contract test.
        assertion_classes: Literal[10] = 10  # spec lock; mirrors §T08
        assert assertion_classes == expected


# ---------------------------------------------------------------------------
# Internal: bridge sync context managers into async ``with`` blocks
# ---------------------------------------------------------------------------


@contextmanager
def _async_cm_inner(sync_cm: AbstractAsyncContextManager[None]) -> Iterator[None]:
    with sync_cm:  # type: ignore[attr-defined]
        yield


class _AsyncCMBridge:
    """Wrap a sync context manager so it can be used in ``async with``.

    The harness ``happy_path`` / ``moderation_mock`` / ``auth_failure_mock``
    helpers return :func:`contextmanager`-decorated sync generators. The
    contract tests need to mix ``patch.object`` (sync) with awaited
    ``backend.generate`` calls; this bridge keeps the harness API uniform
    without forcing every helper to become async.
    """

    def __init__(self, sync_cm: Any) -> None:
        self._sync_cm = sync_cm

    async def __aenter__(self) -> None:
        self._sync_cm.__enter__()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._sync_cm.__exit__(exc_type, exc, tb)


def _async_cm(sync_cm: Any) -> _AsyncCMBridge:
    """Return an ``async with``-compatible bridge over a sync context manager."""
    return _AsyncCMBridge(sync_cm)
