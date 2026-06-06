"""Domain exceptions for the backend layer.

Every error raised by a :class:`persona.backends.protocol.ChatBackend`
implementation is a subclass of :class:`ProviderError`, which in turn is a
subclass of :class:`persona.errors.PersonaError`. Provider-specific exceptions
from third-party SDKs (``anthropic``, ``openai``, ``httpx``) are caught at the
adapter boundary and re-raised through this hierarchy so callers depend on
our types rather than on a transitive dependency.

See ``docs/specs/spec_02/decisions.md`` D-02-1 for the
``BackendTimeoutError`` rationale and D-02-8 for ``retry_after_s`` semantics.
"""

from __future__ import annotations

from persona.errors import PersonaError

__all__ = [
    "AuthenticationError",
    "BackendTimeoutError",
    "BackendVisionNotSupportedError",
    "ModelNotFoundError",
    "NoVisionTierConfiguredError",
    "ProviderError",
    "RateLimitError",
]


class ProviderError(PersonaError):
    """Base for every backend-raised error.

    Non-retryable by default. Subclasses signal specific retry semantics
    (``RateLimitError`` carries an optional ``retry_after_s`` in ``context``;
    ``BackendTimeoutError`` is the canonical retry target).

    Implementations should always populate ``context`` with at least
    ``provider`` and (when known) ``model`` so log messages are structured.
    """


class AuthenticationError(ProviderError):
    """Raised when an API key is missing, invalid, or rejected by the provider.

    Backends raise this at construction time when the configured key is
    missing or empty (fail fast — see spec §10 #8), and at call time when
    the provider returns 401 / 403.
    """


class RateLimitError(ProviderError):
    """Raised when the provider returns 429 (or equivalent).

    When the provider supplies a ``retry-after`` header, it is recorded in
    ``context["retry_after_s"]`` as a string of integer seconds. The header
    is the only source — we never invent a default (D-02-8).
    """


class ModelNotFoundError(ProviderError):
    """Raised when the configured model name is unknown to the provider.

    Maps Anthropic / OpenAI ``NotFoundError`` (model variant) and Ollama's
    ``404 {"error": "model 'xxx' not found"}`` response.
    """


class BackendTimeoutError(ProviderError):
    """Raised when an HTTP request to the provider times out.

    Maps ``httpx.TimeoutException``, ``anthropic.APITimeoutError``, and
    ``openai.APITimeoutError``. Distinct from :class:`ProviderError` because
    timeouts are the most common transient failure callers retry on
    (D-02-1).
    """


# Spec 13 vision errors (D-13-X-error-hierarchy). Both inherit directly
# from PersonaError — no intermediate VisionError parent until a third
# vision-relevant error class lands, per D-03-1's flat-hierarchy rule.


class BackendVisionNotSupportedError(PersonaError):
    """Raised when a vision-incapable backend is asked to serialise an image.

    Fired by the backend message serialisers (Spec 13 T05/T06/T07) when
    a :class:`persona.schema.content.ImageContent` block is present and
    either the backend's ``supports_vision`` property is ``False`` or
    no ``workspace_root`` was configured (so the image bytes cannot be
    resolved).

    The structured ``context`` carries:

    * ``backend`` — the provider/runtime name (e.g. ``"anthropic"``).
    * ``model`` — the configured model name.
    * ``image_count`` — string-formatted count of ImageContent blocks
      in the offending message.
    * ``reason`` — present and set to ``"missing_workspace_root"`` for
      the no-workspace variant; absent for the supports_vision=False
      variant.

    The runtime layer (T11/T12) consumes this on the way back up and
    re-dispatches to the configured vision tier, so the structured
    context is the API contract.
    """


class NoVisionTierConfiguredError(PersonaError):
    """Raised by the runtime router when an image message has no vision tier.

    Fired by the Spec 13 router (T11) when a ConversationMessage carries
    one or more ImageContent blocks and no tier in the configuration has
    ``supports_vision=True``. Distinct from
    :class:`BackendVisionNotSupportedError` because this is a
    *configuration* failure (no tier exists) rather than a *dispatch*
    failure (a specific backend cannot accept the request).

    The structured ``context`` carries:

    * ``reason`` — always ``"no_vision_tier"`` so log filters can match.
    * ``configured_tiers`` — comma-joined list of configured tier names.
    """
