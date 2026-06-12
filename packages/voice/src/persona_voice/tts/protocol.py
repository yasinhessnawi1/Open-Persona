"""Spec V3 streaming-TTS Protocol — the only backend surface V3 callers depend on.

:class:`StreamingTTS` is the provider-independent contract: feed it a
stream of reply text (V5's LLM token stream, an ``AsyncIterator[str]``)
and receive a stream of :class:`persona_voice.loop.streaming.AudioChunk`
records (PCM16 mono 24 kHz, V1's outbound rail). It mirrors the Spec 02
:class:`persona.backends.protocol.ChatBackend` / V2
:class:`persona_voice.stt.protocol.StreamingSTT` shape verbatim — one
Protocol, many providers, the vendor SDK never imported by callers
(Cartesia launch per D-V3-1; ElevenLabs alternative behind the same
seam).

**Spec 02 mirror discipline (D-02-5).** Just like
``ChatBackend.chat_stream`` and ``StreamingSTT.transcripts`` declare
their async-generator method as ``def -> AsyncIterator[...]`` (NOT
``async def``), :meth:`StreamingTTS.synthesize` follows the same pattern:
an async generator's type from the consumer's perspective is
:class:`collections.abc.AsyncIterator` (PEP 525). Concrete
implementations are ``async def`` + ``yield``; consumers iterate with
``async for`` — no extra ``await``.

**This Protocol is the V1 ``TTSStream`` seam, fully specified.** V1
shipped the stub at ``loop/streaming.py:153-164``
(``synthesize(text_stream) -> AsyncIterator[AudioChunk]`` + ``cancel()``).
:class:`StreamingTTS` is that contract plus the introspection +
capability + graceful-close surface V3 needs; the T09 seam adapter
composes a chunker + a concrete ``StreamingTTS`` backend + audio
adaptation into an object that satisfies the V1 ``TTSStream`` Protocol so
it slots into ``StreamingLoop(tts=...)``.

**Cancellation contract (D-V3-5 + D-V3-X-cancel-flush-additive-shape).**
:meth:`cancel` is the V4 barge-in primitive and is exception-free at the
iterator surface: it internally aborts the in-flight provider task and
ends :meth:`synthesize`'s iterator via a sentinel — a consumer parked in
``__anext__`` is unblocked IMMEDIATELY (never a ``CancelledError`` raised
through the audio data plane). It is synchronous-effect-first (marks the
stream cancelled before any awaited teardown) and idempotent (a second
call is a no-op). The full six-step teardown (provider cancel → local
clear → transport-queue clear → watchdog) lives in the T09 seam adapter;
the Protocol pins the surface contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona_voice.loop.streaming import AudioChunk
    from persona_voice.tts.types import ResolvedVoice

__all__ = ["StreamingTTS"]


@runtime_checkable
class StreamingTTS(Protocol):
    """Streaming text-to-speech seam — the V3 backend Protocol.

    Implementations are constructed from a
    :class:`persona_voice.tts.config.StreamingTTSConfig` and expose one
    synthesis operation + cancel/close lifecycle + introspection +
    capability properties:

    * :meth:`synthesize` — consumes an ``AsyncIterator[str]`` of reply
      text (chunked sensibly by the seam adapter's chunker, OR raw tokens
      when :attr:`consumes_raw_text` is ``True``) plus a
      :class:`persona_voice.tts.types.ResolvedVoice` and yields
      :class:`AudioChunk` records (PCM16 mono 24 kHz) as the provider
      synthesises them. Streaming-everywhere: the first audio frame is
      yielded BEFORE the text stream completes (spec §6 criterion #2).
    * :meth:`cancel` — the V4 barge-in primitive (see module docstring).
    * :meth:`close` — graceful shutdown of the provider connection.
    * :attr:`provider_name` / :attr:`model_name` — observability.
    * :attr:`consumes_raw_text` — chunker-placement capability flag
      (D-V3-X-chunker-placement).

    Construction-time failures (missing ``PERSONA_TTS_API_KEY``) raise
    :class:`persona_voice.tts.errors.TTSAuthenticationError` immediately
    per the Spec 02 D-02-10 fail-fast precedent. Provider exceptions
    raised during streaming are caught at the adapter boundary and
    re-raised through the
    :class:`persona_voice.tts.errors.TTSError` hierarchy so callers
    depend on our domain types — never on a transitive SDK.
    """

    @property
    def provider_name(self) -> str:
        """Identifier of the configured TTS provider (``"cartesia"``, ...).

        Lowercase, ASCII. Stable across releases. Used for logging,
        observability, the ``provider`` field of structured audit
        contexts, and the :class:`persona_voice.tts.types.ResolvedVoice`
        ``provider`` match-check at the synthesis boundary.
        """

    @property
    def model_name(self) -> str:
        """Model identifier as the provider knows it (``"sonic-3.5"``, ...).

        Echoed into :class:`persona_voice.logging.VoiceLog` records (T11
        additive TTS fields). Backends do not normalise — what the caller
        configured is what the response reports.
        """

    @property
    def consumes_raw_text(self) -> bool:
        """Whether this backend segments raw token streams server-side.

        The chunker-placement capability flag (D-V3-X-chunker-placement).
        ``False`` (the launch default) means the backend expects
        prosody-coherent, punctuation-terminated chunks and the T09 seam
        adapter MUST run the client-side chunker in front of it (and zero
        any provider-side text buffer so chunking happens exactly once).
        ``True`` means the backend accepts raw LLM tokens and segments
        them server-side (an Azure-class provider); the seam adapter may
        bypass the chunker. Cartesia launch + ElevenLabs alternative both
        return ``False`` per D-V3-2 (client chunker is load-bearing).
        """

    def synthesize(
        self,
        text_stream: AsyncIterator[str],
        voice: ResolvedVoice,
    ) -> AsyncIterator[AudioChunk]:
        """Stream synthesised audio for an incremental reply-text stream.

        Concrete implementations are ``async def`` + ``yield``; the
        Protocol declares plain ``def`` returning
        :class:`collections.abc.AsyncIterator` because that is what an
        async generator's type is from the consumer's perspective
        (PEP 525) — mirrors Spec 02 ``ChatBackend.chat_stream`` D-02-5
        verbatim. Consumers iterate with ``async for``, no extra
        ``await``.

        Args:
            text_stream: Reply text as it is produced. The seam adapter
                feeds prosody-coherent chunks unless
                :attr:`consumes_raw_text` is ``True``. The stream's
                exhaustion is the flush-on-end signal (D-V3-2): the
                backend synthesises any residual buffered text.
            voice: The synthesis-time resolution of the persona's
                ``voice`` (D-V3-X-cloning-seam-shape). The backend asserts
                ``voice.provider == self.provider_name`` and passes
                ``voice.voice_ref`` (plus any provider-interpreted
                ``voice.addressing`` fields) to the provider. The backend
                is deployment-scoped (constructed from
                :class:`persona_voice.tts.config.StreamingTTSConfig`); the
                per-persona voice arrives here, not at construction. The
                T09 seam adapter — which implements V1's voice-free
                ``TTSStream.synthesize(text_stream)`` — holds the resolved
                voice for the session and passes it through.

        Yields:
            :class:`AudioChunk` records (PCM16 mono 24 kHz, V1 outbound
            rail). The FIRST chunk is yielded before ``text_stream``
            completes (criterion #2). On :meth:`cancel` the iterator ends
            via a sentinel — no further chunks, no exception.

        Raises:
            TTSStreamFailureError: provider connection dropped
                mid-utterance (surfaces during iteration, not at call
                time).
            TTSRateLimitError: provider returned 429-equivalent.
            TTSAudioFormatError: provider emitted a non-rail wire format.
        """

    async def cancel(self) -> None:
        """Abort in-flight synthesis for V4 barge-in (D-V3-5).

        Synchronous-effect-first + idempotent: marks the stream cancelled
        (so late provider frames are droppable) before any awaited
        teardown, sends the provider-side cancel where supported (Cartesia
        per-context cancel; else socket-task teardown), and ends
        :meth:`synthesize`'s iterator via a sentinel so a consumer parked
        in ``__anext__`` is unblocked at once. A second call is a no-op.
        The transport-queue clear (``rtc.AudioSource.clear_queue()``) +
        watchdog are the T09 seam adapter's job; this method owns the
        provider + local-generator steps.
        """

    async def close(self) -> None:
        """Close the provider streaming connection gracefully.

        Idempotent — a second call is a no-op, not an error. Provider
        exceptions during close are swallowed (best-effort) rather than
        re-raised; the
        :class:`persona_voice.logging.VoiceLog` audit hop records
        success/failure for observability. Distinct from :meth:`cancel`:
        ``close`` is end-of-session teardown; ``cancel`` is mid-utterance
        barge-in.
        """
