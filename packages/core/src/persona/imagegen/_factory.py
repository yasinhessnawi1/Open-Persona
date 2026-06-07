"""``load_image_backend()`` factory — dispatches an
:class:`ImageBackendConfig` to the right concrete :class:`ImageBackend`.

Mirrors :mod:`persona.backends._factory` per the Spec 15 decisions gate
paragraph #1 ("Mirror Spec 02 verbatim — ``ImageBackend`` is
``ChatBackend``'s shape twin"). The concrete backends
(:class:`persona.imagegen.openai_image.OpenAIImageBackend`,
:class:`persona.imagegen.fal_image.FalImageBackend`) are imported lazily
inside :func:`load_image_backend` so the :mod:`persona.imagegen` package
stays importable before T06 / T07 land — and so callers that only need
the Protocol / config / boundary types do not pay the import cost of the
provider SDKs (``openai`` is already a Spec 02 dep but ``fal-client`` is
new in T07).

References:
    docs/specs/phase2/spec_15/decisions.md gate paragraph #1 + D-15-1
    (provider set ``Literal["openai", "fal"]``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.imagegen.errors import ImageProviderError

if TYPE_CHECKING:
    from persona.imagegen.config import ImageBackendConfig
    from persona.imagegen.protocol import ImageBackend

__all__ = ["load_image_backend"]


_SUPPORTED_PROVIDERS: tuple[str, ...] = ("openai", "fal")
"""Closed set of provider identifiers shipped at v0.1 (D-15-1).

Kept in sync with :data:`persona.imagegen.config.ImageProvider`; the
factory dispatches against this tuple so an unknown provider error
carries the supported list in its message and ``context``.
"""


def load_image_backend(config: ImageBackendConfig) -> ImageBackend:
    """Construct the concrete :class:`ImageBackend` for ``config.provider``.

    Mirrors :func:`persona.backends._factory.load_backend`. Concrete
    backend imports are lazy so :mod:`persona.imagegen` stays importable
    even when the alternate provider SDK is not installed; the concrete
    backend's ``__init__`` still raises a clear
    :class:`persona.imagegen.errors.ImageGenUnavailableError` if its
    SDK or API key is missing.

    Args:
        config: Image backend configuration. ``config.provider`` must be
            one of the values in :data:`_SUPPORTED_PROVIDERS`; otherwise
            :class:`ImageProviderError` is raised.

    Returns:
        A concrete backend implementing the
        :class:`persona.imagegen.protocol.ImageBackend` Protocol.

    Raises:
        ImageGenUnavailableError: ``api_key`` missing or rejected at
            construction time (fail-fast per the Spec 02 D-02-*
            construction-time-fail-fast precedent).
        ImageProviderError: ``config.provider`` is not in the supported
            set; ``context["provider"]`` carries the offending value and
            ``context["supported"]`` the comma-joined supported list.
    """
    provider = config.provider
    if provider == "openai":
        # Lazy import keeps ``persona.imagegen`` importable before T06
        # lands the OpenAI image backend module; the concrete backend's
        # ``__init__`` raises a clear
        # :class:`persona.imagegen.errors.ImageGenUnavailableError` if
        # the ``api_key`` is missing.
        from persona.imagegen.openai_image import (
            OpenAIImageBackend,
        )

        return OpenAIImageBackend(config)
    if provider == "fal":
        # Lazy import keeps ``persona.imagegen`` importable before T07
        # lands the fal backend module (``fal-client`` is a new dep
        # declared in ``packages/core/pyproject.toml`` by T07); the
        # concrete backend's ``__init__`` raises a clear
        # :class:`persona.imagegen.errors.ImageGenUnavailableError` if
        # the ``api_key`` is missing.
        from persona.imagegen.fal_image import (
            FalImageBackend,
        )

        return FalImageBackend(config)
    supported = ", ".join(_SUPPORTED_PROVIDERS)
    raise ImageProviderError(
        f"unknown image provider {provider!r}; expected one of {supported}",
        context={"provider": str(provider), "supported": supported},
    )
