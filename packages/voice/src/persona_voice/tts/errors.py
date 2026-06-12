"""Domain exceptions for the V3 streaming-TTS layer.

Every error raised from V3's streaming-TTS path is a subclass of
:class:`TTSError`, which itself inherits from
:class:`persona.errors.PersonaError`. Provider-specific exceptions from
third-party SDKs (the Cartesia WebSocket SDK, ``httpx`` transport errors,
the ElevenLabs alternative SDK) are caught at the adapter boundary and
re-raised through this hierarchy so callers depend on our types rather
than on a transitive dependency.

**Rooting at PersonaError, not ProviderError.** This mirrors the Spec 02
:class:`persona.backends.errors.ProviderError` *shape* (one base + a
parallel set of failure-mode subclasses) verbatim — the ChatBackend /
V2 ``STTError`` mirror discipline — but roots at
:class:`persona.errors.PersonaError` directly, exactly as V2's
``STTError`` does (``stt/errors.py:35``). Voice-layer errors are
deliberately kept OUT of the :class:`persona.backends.errors.ProviderError`
partition that the Spec 20 ``MultiModelChatBackend`` fallback classifier
(D-20-9 / D-20-16) walks — a TTS stream failure must never be swept into
the chat-tier retry/fallback machinery.

Each subclass docstring states the discrete failure mode it covers;
populate the ``context`` dict with at least ``provider`` (and, when
known, ``model`` / ``voice``) so log messages are structured per the
:class:`persona.errors.PersonaError` convention.
"""

from __future__ import annotations

from persona.errors import PersonaError

__all__ = [
    "TTSAudioFormatError",
    "TTSAuthenticationError",
    "TTSError",
    "TTSRateLimitError",
    "TTSStreamFailureError",
    "TTSVoiceNotFoundError",
]


class TTSError(PersonaError):
    """Base for every V3 streaming-TTS-raised error.

    Non-retryable by default. Subclasses signal specific failure modes —
    :class:`TTSRateLimitError` may carry a ``retry_after_s`` string in
    ``context``; :class:`TTSStreamFailureError` is the canonical
    mid-utterance-disconnect target (the persona falls silent cleanly
    rather than emitting garbage audio, spec §6 criterion #11).

    Implementations should always populate ``context`` with at least
    ``provider`` and (when known) ``model`` so log records carry the
    structured data callers need — same convention as Spec 02's
    :class:`persona.backends.errors.ProviderError`.
    """


class TTSAuthenticationError(TTSError):
    """Raised when the TTS provider API key is missing, invalid, or rejected.

    Fired at construction time when ``PERSONA_TTS_API_KEY`` is missing or
    empty (fail-fast per the Spec 02 D-02-10 + V3 D-V3-X-cost discipline),
    and at call time when the provider returns 401 / 403.
    """


class TTSRateLimitError(TTSError):
    """Raised when the provider returns 429 (or equivalent).

    When the provider supplies a ``retry-after`` header it is recorded in
    ``context["retry_after_s"]`` as a string of integer seconds — mirrors
    Spec 02 D-02-8 semantics: the header is the only source; we never
    invent a default.
    """


class TTSStreamFailureError(TTSError):
    """Raised when the provider streaming connection disconnects mid-utterance.

    Covers Cartesia WebSocket close-with-error, transport drops mid-stream,
    and the ElevenLabs alternative's stream-failure events. This is the
    graceful-fall-silent target of spec §6 criterion #11: the loop does
    not crash; the persona stops emitting audio with a recoverable state
    rather than producing garbage. Carries any in-flight context (e.g.
    ``context_id``) in ``context`` for V4 + audit.
    """


class TTSAudioFormatError(TTSError):
    """Raised when the provider's audio output cannot meet the V1 rail.

    V1's D-V1-6 invariant pins the outbound rail to PCM16 mono 24 kHz; all
    launch + alternative providers per D-V3-1 + R-V3-4 emit that natively
    (zero transcoding). This error fires when the wire format nonetheless
    deviates — typically an operator misconfiguring ``PERSONA_TTS_*`` away
    from the canonical shape, or a provider returning a container header
    mid-stream (e.g. a RIFF/WAV header where ``raw`` was requested). Per
    D-V3-X-no-pacing-t06 + R-V3-4 this is a startup/config bug surfaced
    fast, never patched over at runtime. Carries the offending
    sample-rate / encoding in ``context``.
    """


class TTSVoiceNotFoundError(TTSError):
    """Raised when a persona's ``voice`` cannot be resolved to a provider voice.

    The V3-specific error of the cloning-seam resolution path
    (D-V3-X-cloning-seam-shape): ``resolve_voice`` raises this when the
    configured provider is unknown, the voice id is not in the provider's
    allowed catalogue set, or a persona has no ``voice`` and no
    ``PERSONA_TTS_VOICE_DEFAULT`` fallback is configured (D-V3-4). It is
    deliberately a *resolution-time, fallible* error — the same path a
    future v0.2 cloned-voice readiness/consent-revoked check reuses
    (``VoiceConsentRevokedError`` slots beside it later). Carries
    ``{"provider", "voice"}`` in ``context``.
    """
