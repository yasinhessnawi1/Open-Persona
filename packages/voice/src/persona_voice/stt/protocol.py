"""Spec V2 streaming-STT Protocols — the only surface V2 callers depend on.

Two Protocols live here; together they realise the sensor-vs-decider
boundary D-V2-2 + R-V2-2 + Pipecat issue #1323 production-bug precedent
all converge on:

* :class:`StreamingSTT` — push-model streaming-STT seam. Mirrors the
  Spec 02 :class:`persona.backends.protocol.ChatBackend` Protocol shape
  verbatim (``protocol.py:25-26``). Every concrete backend (Deepgram
  Nova-3 launch per D-V2-1 LOCK; Speechmatics Ursa 2 alternative behind
  the same Protocol) implements this. Callers depend on the Protocol —
  never on a provider SDK or transport.
* :class:`SpeechActivityListener` — separate listener Protocol the
  Silero VAD adapter (T05) calls into. Keeping it OFF the
  :class:`StreamingSTT` Protocol is the lesson Pipecat issue #1323
  paid for in production: bundling a speech-activity stream alongside
  a transcript stream invites frame re-ordering across the shared seam
  (4× duplicate emissions in the cited bug). The split keeps each
  consumer focused on a single stream contract.

**Spec 02 mirror discipline.** Just like ``ChatBackend.chat_stream``
declares its async-generator method as ``def -> AsyncIterator[StreamChunk]``
(not ``async def``) per D-02-5, :meth:`StreamingSTT.transcripts` follows
the same pattern: an async generator's type from the consumer's
perspective is :class:`collections.abc.AsyncIterator` (PEP 525). Concrete
implementations are ``async def`` + ``yield``; consumers iterate with
``async for`` — no extra ``await``.

**``close()`` / flush semantics.** Per R-V2-4 (LiveKit / Pipecat / Vapi
prior-art review), per-backend variability on graceful-vs-abort close is
material: Deepgram's WebSocket close finalises in-flight buffers and
emits one last FINAL transcript before the close-frame; Speechmatics
Real-time exposes an explicit ``EndOfStream`` message. The Protocol
contract is the union: callers MAY ``await stt.close()`` and continue
to drain :meth:`transcripts` until the iterator stops — a backend that
discards in-flight audio MUST still terminate the iterator cleanly.
Implementations document their own flush behaviour in the concrete
backend module docstring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona_voice.loop.streaming import Transcript
    from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent

__all__ = ["SpeechActivityListener", "StreamingSTT"]


@runtime_checkable
class StreamingSTT(Protocol):
    """Push-model streaming-STT seam — the V2 backend Protocol.

    Implementations are constructed from a
    :class:`persona_voice.stt.config.StreamingSTTConfig` and expose four
    operations + two introspection properties:

    * :meth:`push_audio` — feeds PCM16 16 kHz mono frames from V1's
      inbound rail into the provider's streaming socket. V1's D-V1-6
      invariant pins inbound audio to PCM16 mono 16 kHz; both the
      Deepgram launch and Speechmatics alternative per D-V2-1 accept
      this format natively (zero transcoding).
    * :meth:`transcripts` — yields :class:`Transcript` records
      (partials + finals) as the recogniser produces them. Concrete
      implementations are async generators; consumers iterate with
      ``async for``.
    * :meth:`close` — graceful shutdown. Per-backend variability on
      flush semantics is documented in this module's docstring; the
      contract is that callers MAY continue to drain
      :meth:`transcripts` after ``await stt.close()`` until the
      iterator terminates.
    * :attr:`provider_name` / :attr:`model_name` — observability.

    Construction-time failures (missing ``PERSONA_STT_API_KEY``) raise
    :class:`persona_voice.stt.errors.STTAuthenticationError` immediately
    per the Spec 02 D-02-10 fail-fast precedent. Provider exceptions
    raised during streaming are caught at the adapter boundary and
    re-raised through the
    :class:`persona_voice.stt.errors.STTError` hierarchy so callers
    depend on our domain types — never on a transitive SDK.
    """

    @property
    def provider_name(self) -> str:
        """Identifier of the configured STT provider (``"deepgram"``, ...).

        Lowercase, ASCII. Stable across releases. Used for logging,
        observability, and the ``provider`` field of structured audit
        contexts.
        """

    @property
    def model_name(self) -> str:
        """Model identifier as the provider knows it (``"nova-3"``, ...).

        Echoed back in :class:`persona_voice.logging.VoiceLog` records
        (T08 additive STT fields). Backends do not normalise — what the
        caller configured is what the response reports.
        """

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        """Feed one inbound PCM16 audio frame into the provider socket.

        Args:
            pcm: Raw PCM16 little-endian bytes for one frame.
            sample_rate: The frame's sample rate in Hz. V1's D-V1-6
                invariant pins this to 16000 on the inbound rail;
                implementations MAY reject other rates with
                :class:`persona_voice.stt.errors.STTAudioFormatError`
                or transcode at the adapter boundary per R-V2-3.

        Raises:
            STTAudioFormatError: rejected sample-rate / encoding.
            STTStreamFailureError: provider socket disconnected
                mid-utterance.
            STTRateLimitError: provider returned 429-equivalent.
        """

    def transcripts(self) -> AsyncIterator[Transcript]:
        """Stream of :class:`Transcript` records — partials and finals.

        Concrete implementations are ``async def`` + ``yield``; the
        Protocol declares plain ``def`` returning
        :class:`collections.abc.AsyncIterator` because that is what an
        async generator's type is from the consumer's perspective
        (PEP 525). Consumers iterate with ``async for``, no extra
        ``await``. Mirrors Spec 02's :meth:`ChatBackend.chat_stream`
        D-02-5 precedent verbatim.

        Yields:
            :class:`Transcript` records with ``is_final=False`` for
            partials and ``is_final=True`` for finals. The
            ``eou_at`` field on finals samples T08's
            stt_partial_first_at / stt_audio_pushed_at latency hops.

        Raises: see :meth:`push_audio`. Errors from the provider
            surface during iteration, not at call time.
        """

    async def close(self) -> None:
        """Close the provider streaming socket gracefully.

        Per-backend flush variability is material (see module docstring):
        callers MAY continue draining :meth:`transcripts` after this
        call returns until the iterator terminates. Implementations
        MUST make :meth:`close` idempotent — a second call is a no-op,
        not an error. Provider exceptions during close are swallowed
        (best-effort) rather than re-raised; the
        :class:`persona_voice.logging.VoiceLog` audit hop records
        success/failure for observability.
        """

    def speech_activity_events(
        self,
    ) -> AsyncIterator[SpeechStartedEvent | SpeechEndedEvent]:
        """Provider-side speech-activity event stream (corroborator path).

        Per D-V2-2 + R-V2-2 combination_design: provider endpointing
        events (Deepgram ``UtteranceEnd``, Speechmatics ``EndOfStream``,
        etc.) are exposed here for the T06 seam adapter to wire as
        CORROBORATORS of the Silero VAD primary stream. Silero remains
        the AUTHORITATIVE onset source; provider events publish on the
        :class:`SpeechActivityListener` port with
        ``source="provider"`` + ``corroborates=True`` so V4 can weight
        provider-confirmed endpoints higher.

        Backends with no provider-side activity surface MAY return an
        empty async iterator (yield nothing); the seam adapter's
        drainer task exits cleanly in that case.

        Yields:
            :class:`SpeechStartedEvent` and :class:`SpeechEndedEvent`
            records with ``source="provider"``.
        """


@runtime_checkable
class SpeechActivityListener(Protocol):
    """Speech-activity port the Silero VAD adapter (T05) writes to.

    Kept OFF :class:`StreamingSTT` deliberately. Bundling a
    speech-activity stream alongside a transcript stream is the exact
    shape Pipecat issue #1323 paid for in production (4× duplicate
    emissions caused by frame re-ordering across a shared seam). The
    sensor-vs-decider separation per D-V2-X-activity-listener-shape:

    * **Sensor side** (T05 Silero VAD adapter, T04 Deepgram backend's
      provider-endpointing corroborator) writes raw events on this
      Protocol.
    * **Decider side** (V4 barge-in policy, future) reads the activity
      stream and interprets transitions semantically.

    Listeners are called from the V1 inbound-frame dispatch loop
    (T07 additive port at ``streaming.py:198-217``); they MUST be
    cheap and non-blocking — slow listeners stall the audio rail.
    """

    async def on_speech_started(self, event: SpeechStartedEvent) -> None:
        """Notify the listener that a speech-onset transition was detected.

        Args:
            event: The frozen :class:`SpeechStartedEvent` record from
                the sensor. Carries ``ts_audio_s`` (position in the
                inbound stream) + ``ts_emit`` (wall-clock UTC) +
                ``source`` (which sensor produced the event) +
                ``confidence`` (Silero VAD probability when
                ``source="silero"``).

        Implementations MUST NOT raise — exceptions stall the inbound
        audio dispatch loop. Log + swallow at the listener boundary.
        """

    async def on_speech_ended(self, event: SpeechEndedEvent) -> None:
        """Notify the listener that a speech-offset transition was detected.

        Args:
            event: The frozen :class:`SpeechEndedEvent` record. Carries
                the boundary fields plus ``transcript_settled`` (whether
                a provider FINAL has already fired for this segment),
                ``corroborates`` (true for second-source corroborations
                per R-V2-2 at-least-once delivery), and
                ``silence_duration_ms`` (sensor-measured offset window).

        Same non-raise contract as :meth:`on_speech_started`.
        """
