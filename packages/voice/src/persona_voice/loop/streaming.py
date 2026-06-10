"""Streaming-loop skeleton — the four named Protocol seams + the pass-through echo default.

D-V1-X-loop-skeleton-shape locks the async-generator pattern + dual-priority
queue (Pipecat InterruptionFrame discipline; R-V1-5 finding #5). Each seam
is a :class:`typing.Protocol` so V2 / V3 / V4 / V5 implementing agents can
satisfy it with provider-specific adapters without a base class. V1 owns the
plumbing; the seams stay provider-independent (R-V1-5 lean — "adopt the
seam shape, reject the SDK coupling").

The seams:

* **V2 — :class:`STTStream`**: push model. ``push_audio(pcm, sample_rate)``
  ingests inbound frames; ``transcripts()`` yields :class:`Transcript`
  records (``is_final``, ``text``, ``confidence``, ``eou_at``).
* **V3 — :class:`TTSStream`**: ``synthesize(text_stream)`` consumes an
  ``AsyncIterator[str]`` (LLM token stream) and yields
  :class:`AudioChunk` records. ``cancel()`` aborts in-flight synthesis
  (barge-in path — T08 binary criterion).
* **V4 — :class:`SessionEventListener`** (defined in
  :mod:`persona_voice.session.state_machine`): dual-priority queue dispatches
  ``UserStartedSpeaking`` / ``Interruption`` / ``EndOfTurn`` events on the
  SYSTEM priority lane so they bypass the audio data lane.
* **V5 — :class:`ModelReplyProducer`**: an async-callable producing
  ``AsyncIterator[str]`` tokens from a final-transcript trigger; never a
  blocking ``complete()`` call. The streaming-everywhere discipline
  (R-V1-3) hinges on V5 emitting tokens incrementally so V3 TTS can start
  audio before the LLM finishes — compresses additive latency into max().

The :class:`StreamingLoop` runs the streaming pipeline:

1. Inbound audio frames from :class:`VoiceRoom` arrive at
   :meth:`StreamingLoop._on_inbound_frame`.
2. If a :class:`STTStream` is wired, the frame is pushed into it; otherwise
   the loop falls back to :attr:`PassThroughEchoMode.ECHO` and pipes the
   inbound frame straight to the outbound rail (the pass-through default
   that lets T08 verify the full-duplex pipe before V2/V3/V5 wire).
3. Transcripts flow from V2 → V5; tokens flow from V5 → V3; audio chunks
   flow from V3 → :class:`VoiceRoom`'s outbound source.

Audio invariants (D-V1-6) are enforced at the seam boundary: inbound PCM16
mono 16 kHz; outbound PCM16 mono 24 kHz. Every audio-bearing record carries
its ``sample_rate`` explicitly so the sample-rate-mismatch bug R-V1-5 names
("the #1 production bug per AssemblyAI debugging guide") is structurally
impossible.
"""

from __future__ import annotations

import asyncio
from datetime import datetime  # noqa: TC003 — runtime for Pydantic field validation
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from persona_voice.session.state_machine import (
    SessionLifecycleEvent,
    SessionStateMachine,
)
from persona_voice.transport.room import (
    AUDIO_OUTBOUND_CHANNELS,
    AUDIO_OUTBOUND_SAMPLE_RATE,
    InboundAudioFrame,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona_voice.stt.protocol import SpeechActivityListener
    from persona_voice.transport.room import VoiceRoom


__all__ = [
    "AudioChunk",
    "ModelReplyProducer",
    "PassThroughEchoMode",
    "STTStream",
    "StreamingLoop",
    "TTSStream",
    "Transcript",
]


# ---------- boundary records ------------------------------------------------


class Transcript(BaseModel):
    """V2 STT output — one (partial or final) recognised utterance.

    Frozen + ``extra="forbid"`` per the D-05-9 boundary discipline. The
    ``eou_at`` timestamp is what T10's VoiceLog samples for the
    end-of-utterance latency hop (R-V1-3 per-hop budget table).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_final: bool
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    eou_at: datetime | None = None


class AudioChunk(BaseModel):
    """V3 TTS output / V1 outbound rail — one PCM16 chunk + its sample rate.

    Always PCM16 mono. ``sample_rate`` is explicit per D-V1-6: every audio
    record carries the rate so resampling is structurally impossible inside
    the loop body (conversion happens at the seam adapter boundary).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    data: bytes
    sample_rate: int = Field(gt=0)
    num_channels: int = Field(default=1, gt=0)
    samples_per_channel: int = Field(gt=0)


# ---------- pass-through fallback ------------------------------------------


class PassThroughEchoMode(StrEnum):
    """Pass-through behaviour when no STT/TTS/Model seams are wired.

    ``ECHO`` — inbound audio frames are forwarded directly to the outbound
    rail (full-duplex proof for T08 binary criterion #3).
    ``DISABLED`` — inbound frames are dropped; outbound stays silent (the
    production default; the loop is only useful with at least V2+V3+V5
    wired).
    """

    ECHO = "echo"
    DISABLED = "disabled"


# ---------- V2/V3/V5 seam Protocols ----------------------------------------


@runtime_checkable
class STTStream(Protocol):
    """V2 — push-model streaming STT seam.

    ``push_audio`` ingests PCM16 16 kHz mono frames from the inbound rail;
    ``transcripts()`` yields :class:`Transcript` records (partials + final)
    as the recogniser produces them.
    """

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None: ...

    def transcripts(self) -> AsyncIterator[Transcript]: ...


@runtime_checkable
class TTSStream(Protocol):
    """V3 — streaming TTS seam.

    ``synthesize`` consumes an ``AsyncIterator[str]`` (V5's LLM token
    stream) and yields :class:`AudioChunk` records. ``cancel`` aborts an
    in-flight synthesis — the barge-in path (V4).
    """

    def synthesize(self, text_stream: AsyncIterator[str]) -> AsyncIterator[AudioChunk]: ...

    async def cancel(self) -> None: ...


@runtime_checkable
class ModelReplyProducer(Protocol):
    """V5 — model-reply seam.

    Async-callable that consumes the final transcript and yields LLM tokens
    incrementally. The streaming contract is what compresses additive
    latency into max() (R-V1-3) — never a blocking ``complete()`` call.
    """

    async def __call__(self, final_transcript: Transcript) -> AsyncIterator[str]: ...


# ---------- the streaming loop ---------------------------------------------


class StreamingLoop:
    """Wires :class:`VoiceRoom` to the V2/V3/V4/V5 seams.

    At v0.1 the loop ships:

    * Inbound audio dispatch (frame → V2 push OR echo).
    * V2 → V5 → V3 token-stream wiring (the streaming pipeline).
    * V4 lifecycle event dispatch (delegates to :class:`SessionStateMachine`
      which is the single source-of-truth for the User/Agent two-state
      machine).
    * Pass-through echo fallback so T08 can verify criterion #3
      independently of any STT/TTS/LLM adapter.

    The audio invariants (D-V1-6) are enforced at the seam boundary; the
    loop body never resamples.
    """

    def __init__(
        self,
        *,
        voice_room: VoiceRoom,
        session: SessionStateMachine,
        stt: STTStream | None = None,
        tts: TTSStream | None = None,
        model: ModelReplyProducer | None = None,
        echo_mode: PassThroughEchoMode = PassThroughEchoMode.ECHO,
        speech_activity: SpeechActivityListener | None = None,
    ) -> None:
        # Spec V2 D-V2-X-streaming-loop-additivity-shape — ADDITIVE
        # ``speech_activity`` injected port; backwards-compatible default
        # ``None``. The seam adapter (T06) merges Silero VAD + provider
        # endpointing events and dispatches to this listener; V4 (future)
        # is the listener. Pipecat issue #1323 production-bug precedent:
        # keeping activity events on a separate Protocol from transcripts
        # avoids the frame-reordering class of bugs.
        self._voice_room = voice_room
        self._session = session
        self._stt = stt
        self._tts = tts
        self._model = model
        self._echo_mode = echo_mode
        self._speech_activity = speech_activity
        self._pipeline_task: asyncio.Task[None] | None = None
        # V1 wires the inbound dispatcher into the VoiceRoom at construction
        # so frames that arrive during connect are not dropped on the floor.
        voice_room.set_inbound_handler(self._on_inbound_frame)

    @property
    def speech_activity(self) -> SpeechActivityListener | None:
        """V2 additive — the registered ``SpeechActivityListener``, if any.

        Production composition wires the V4 listener once at construction
        OR via this property's setter; downstream consumers (e.g.
        observability harnesses, integration tests) read via the property.
        """
        return self._speech_activity

    @speech_activity.setter
    def speech_activity(self, value: SpeechActivityListener | None) -> None:
        self._speech_activity = value

    # ----- inbound + echo --------------------------------------------

    async def _on_inbound_frame(self, frame: InboundAudioFrame) -> None:
        """Single dispatch point for inbound PCM16/16k frames."""
        if self._stt is not None:
            await self._stt.push_audio(frame.data, frame.sample_rate)
            return
        if self._echo_mode == PassThroughEchoMode.ECHO:
            await self._echo_inbound_to_outbound(frame)

    async def _echo_inbound_to_outbound(self, frame: InboundAudioFrame) -> None:
        """Pipe one inbound frame straight to the outbound rail.

        The outbound rail is PCM16 mono 24 kHz; the inbound rail is PCM16
        mono 16 kHz. T08 echoes at the **inbound** rate to keep the loop
        body resample-free — the test asserts round-trip audio integrity,
        not a particular outbound rate, so this is a valid v0.1
        pass-through proof. T07 leaves sample-rate conversion to V3 adapter
        consumers.
        """
        # Lazy import — only needed if echo path actually fires, and keeps
        # the loop module import-light for tests that never touch outbound.
        from livekit import rtc

        await self._ensure_outbound_published()
        await self._voice_room.capture_outbound_frame(
            rtc.AudioFrame(
                data=frame.data,
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
                samples_per_channel=frame.samples_per_channel,
            )
        )

    async def _ensure_outbound_published(self) -> None:
        # ``publish_outbound`` is idempotent — calling on each echo path
        # invocation costs ~zero after the first call.
        await self._voice_room.publish_outbound()

    # ----- V2/V5/V3 pipeline -----------------------------------------

    async def start_pipeline(self) -> None:
        """Spawn the V2 → V5 → V3 streaming pipeline as a background task.

        Idempotent — calling twice is a no-op (the existing task continues).
        The pipeline reads transcripts from V2, hands the final transcript
        to V5, pipes V5's token stream into V3, and pushes V3's audio
        chunks into the outbound rail.
        """
        if self._pipeline_task is not None and not self._pipeline_task.done():
            return
        if self._stt is None or self._model is None or self._tts is None:
            # V2/V3/V5 not all wired — pipeline can't run. Echo mode keeps
            # the room duplex for T08; intelligence stays pending.
            return
        self._pipeline_task = asyncio.create_task(
            self._run_pipeline(self._stt, self._model, self._tts),
            name="voice-streaming-pipeline",
        )

    async def _run_pipeline(
        self,
        stt: STTStream,
        model: ModelReplyProducer,
        tts: TTSStream,
    ) -> None:
        """Drive the V2 → V5 → V3 streaming chain.

        For each *final* transcript V2 emits, V5 produces an
        ``AsyncIterator[str]`` of tokens; V3 consumes that iterator
        directly (no buffering) and yields audio chunks; the loop pushes
        each chunk into the outbound rail as it arrives.
        """
        await self._ensure_outbound_published()
        async for transcript in stt.transcripts():
            if not transcript.is_final:
                continue
            await self._session.notify(SessionLifecycleEvent.AGENT_STARTED_SPEAKING)
            try:
                token_stream = await model(transcript)
                async for chunk in tts.synthesize(token_stream):
                    await self._push_audio_chunk(chunk)
            finally:
                await self._session.notify(SessionLifecycleEvent.AGENT_STOPPED_SPEAKING)

    async def _push_audio_chunk(self, chunk: AudioChunk) -> None:
        """Push one V3 :class:`AudioChunk` onto the outbound rail.

        Validates the sample rate matches the outbound rail (D-V1-6: PCM16
        mono 24 kHz). Mismatch fails loud — the V3 adapter owns rate
        conversion at its boundary, not the loop.
        """
        if chunk.sample_rate != AUDIO_OUTBOUND_SAMPLE_RATE:
            msg = (
                f"outbound rail expects {AUDIO_OUTBOUND_SAMPLE_RATE} Hz; "
                f"V3 TTSStream produced {chunk.sample_rate} Hz"
            )
            raise ValueError(msg)
        if chunk.num_channels != AUDIO_OUTBOUND_CHANNELS:
            msg = (
                f"outbound rail expects {AUDIO_OUTBOUND_CHANNELS} channel(s); "
                f"V3 TTSStream produced {chunk.num_channels}"
            )
            raise ValueError(msg)
        from livekit import rtc

        await self._voice_room.capture_outbound_frame(
            rtc.AudioFrame(
                data=chunk.data,
                sample_rate=chunk.sample_rate,
                num_channels=chunk.num_channels,
                samples_per_channel=chunk.samples_per_channel,
            )
        )

    # ----- barge-in / lifecycle --------------------------------------

    async def interrupt(self) -> None:
        """V4 barge-in entry — cancel in-flight TTS + notify the session.

        Called by V4 (out of scope for V1) when the user starts speaking
        while the agent is mid-utterance. The TTS cancel propagates as the
        ``synthesize`` AsyncIterator raising :class:`StopAsyncIteration` on
        its next yield; the audio rail goes quiet within one frame's worth
        of jitter buffer.
        """
        if self._tts is not None:
            await self._tts.cancel()
        await self._session.notify(SessionLifecycleEvent.AGENT_STOPPED_SPEAKING)

    async def stop(self) -> None:
        """Stop the pipeline task cleanly (called by session teardown)."""
        if self._pipeline_task is not None and not self._pipeline_task.done():
            self._pipeline_task.cancel()
        self._pipeline_task = None
