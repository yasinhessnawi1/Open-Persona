"""Domain exceptions for the V2 streaming-STT layer.

Every error raised from V2's streaming-STT path is a subclass of
:class:`STTError`, which itself inherits from
:class:`persona.errors.PersonaError`. Provider-specific exceptions
from third-party SDKs (Deepgram WebSocket errors, ``httpx`` transport
errors, the Speechmatics alternative SDK) are caught at the adapter
boundary and re-raised through this hierarchy so callers depend on
our types rather than on a transitive dependency.

Mirrors the Spec 02 :class:`persona.backends.errors.ProviderError`
shape at
``packages/core/src/persona/backends/errors.py:30-75`` verbatim — the
ChatBackend mirror discipline V2 inherits per
`tasks.md` §"Spec 02 mirror discipline". Each subclass docstring
states the discrete failure mode it covers; populate the ``context``
dict with at least ``provider`` (and, when known, ``model``) so log
messages are structured per the
:class:`persona.errors.PersonaError` convention.
"""

from __future__ import annotations

from persona.errors import PersonaError

__all__ = [
    "STTAudioFormatError",
    "STTAuthenticationError",
    "STTError",
    "STTRateLimitError",
    "STTStreamFailureError",
]


class STTError(PersonaError):
    """Base for every V2 streaming-STT-raised error.

    Non-retryable by default. Subclasses signal specific retry
    semantics — :class:`STTRateLimitError` may carry a
    ``retry_after_s`` string in ``context``;
    :class:`STTStreamFailureError` is the canonical reconnect target.

    Implementations should always populate ``context`` with at least
    ``provider`` and (when known) ``model`` so log records carry the
    structured data callers need — same convention as Spec 02's
    :class:`persona.backends.errors.ProviderError`.
    """


class STTAuthenticationError(STTError):
    """Raised when the STT provider API key is missing, invalid, or rejected.

    Fired at construction time when ``PERSONA_STT_API_KEY`` is missing
    or empty (fail-fast per the Spec 02 D-02-10 + V2 D-V2-X-cost-discipline
    discipline), and at call time when the provider returns 401 / 403.
    """


class STTRateLimitError(STTError):
    """Raised when the provider returns 429 (or equivalent).

    When the provider supplies a ``retry-after`` header, it is recorded
    in ``context["retry_after_s"]`` as a string of integer seconds —
    mirrors Spec 02 D-02-8 semantics: the header is the only source;
    we never invent a default.
    """


class STTStreamFailureError(STTError):
    """Raised when the provider streaming connection disconnects mid-utterance.

    Covers Deepgram WebSocket close-with-error, transport drops mid-stream,
    and the Speechmatics alternative's session-failure events. Distinct
    from :class:`STTError` because mid-utterance disconnects are the
    most common transient failure callers reconnect on — the V2 backend
    surfaces partial-transcripts-lost in ``context`` for V4 + audit.
    """


class STTAudioFormatError(STTError):
    """Raised when the provider rejects the audio format on stream open.

    V1's D-V1-6 invariant pins inbound audio to PCM16 mono 16 kHz; both
    the launch (Deepgram Nova-3) and alternative (Speechmatics Ursa 2)
    backends per D-V2-1 + R-V2-3 accept this format natively (zero
    transcoding). This error fires when the wire-format negotiation
    nonetheless fails — typically when an operator misconfigures
    ``PERSONA_STT_*`` env vars away from the canonical shape. Carries
    the rejected sample-rate / encoding in ``context``.
    """
