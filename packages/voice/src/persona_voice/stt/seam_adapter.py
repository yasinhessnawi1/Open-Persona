"""V1 ``STTStream`` seam adapter — production composition root for Spec V2.

Composes :class:`~persona_voice.stt.protocol.StreamingSTT` (Deepgram, etc.)
+ :class:`~persona_voice.stt.vad_silero.SileroVADAdapter` into an object
satisfying V1's :class:`persona_voice.loop.streaming.STTStream` Protocol.
V1's :class:`~persona_voice.loop.streaming.StreamingLoop` receives this as
its ``stt`` port; V4's ``SpeechActivityListener`` (out of scope for V2)
receives the merged activity-event stream via T07's additive
``speech_activity`` port.

**Why the adapter exists.** V1's ``STTStream`` Protocol carries transcripts
only (the kickoff "sensor vs decider" rule — V1's loop knows nothing about
activity events). V2 needs BOTH a transcript stream AND an activity-event
stream on a separate Protocol (D-V2-X-activity-listener-shape; Pipecat
issue #1323 production-bug precedent). The seam adapter is the composition
root that wires both surfaces without touching V1 source. V2's only V1
source edit lands at T07.

**Two-source activity merge per D-V2-2 + R-V2-2 combination_design.**

* ``speech_started`` — Silero VAD is the AUTHORITATIVE source. Provider
  onset events (Deepgram ``SpeechStarted``) are NOT consumed in v0.1
  because the network RTT (20-40 ms one-way) erodes the ≤100 ms Jarvis
  onset budget.
* ``speech_ended`` — Silero VAD is the PRIMARY source. Provider
  endpointing events (Deepgram ``UtteranceEnd``) are wired as
  CORROBORATORS: published as a SECOND listener notification with
  ``source="provider"`` + ``corroborates=True`` so V4 can weight provider-
  confirmed endpoints higher without depending on the provider signal for
  the sensor to function at all.

**Conflict-resolution rules (R-V2-2 combination_design).** The sensor never
blocks; every event publishes as soon as its source fires. Cross-source
ordering is NOT guaranteed — V4 must tolerate provider end arriving after
Silero start of the next utterance.

**Lifecycle.** :meth:`load` is forwarded to :meth:`SileroVADAdapter.load`
for explicit prewarm (D-V2-X-silero-implementation-shape pillar #3).
:meth:`push_audio` tees the inbound PCM16 bytes to both the backend and the
VAD. Background drainer tasks pump activity events from both sources into
the listener; :meth:`close` cancels the drainers + closes both sources
idempotently.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona_voice.loop.streaming import Transcript
    from persona_voice.stt.protocol import (
        SpeechActivityListener,
        StreamingSTT,
    )
    from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent
    from persona_voice.stt.vad_silero import SileroVADAdapter


__all__ = ["V1STTStreamSeamAdapter"]


class V1STTStreamSeamAdapter:
    """Composes a V2 :class:`StreamingSTT` + :class:`SileroVADAdapter`.

    Implements V1's ``STTStream`` Protocol (``push_audio`` + ``transcripts``)
    structurally — V1's :class:`~persona_voice.loop.streaming.StreamingLoop`
    constructor receives this instance as the ``stt`` port.

    The activity-event stream is published on the V4
    :class:`~persona_voice.stt.protocol.SpeechActivityListener` port (a
    separate Protocol per D-V2-X-activity-listener-shape; T07 wires the
    additive ``speech_activity`` ctor param at
    ``loop.streaming.StreamingLoop.__init__``).
    """

    def __init__(
        self,
        *,
        backend: StreamingSTT,
        vad: SileroVADAdapter,
        listener: SpeechActivityListener | None = None,
    ) -> None:
        """Wire the backend + VAD + optional listener.

        Args:
            backend: Concrete :class:`StreamingSTT` (Deepgram at v0.1; the
                alternative-provider story per D-V2-1 swaps Speechmatics
                behind the same Protocol seam).
            vad: :class:`SileroVADAdapter` providing the AUTHORITATIVE
                onset + PRIMARY offset stream.
            listener: Optional :class:`SpeechActivityListener` consumer.
                If ``None`` at construction, set via :attr:`listener` later
                (T07 wiring path).
        """
        self._backend = backend
        self._vad = vad
        self._listener: SpeechActivityListener | None = listener
        self._vad_drain_task: asyncio.Task[None] | None = None
        self._provider_drain_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def listener(self) -> SpeechActivityListener | None:
        return self._listener

    @listener.setter
    def listener(self, value: SpeechActivityListener | None) -> None:
        self._listener = value

    async def load(self) -> None:
        """Prewarm the Silero VAD ONNX session.

        Per D-V2-X-silero-implementation-shape pillar #3, call at
        agent-worker process init (NOT first-frame). Idempotent.
        """
        await self._vad.load()

    async def push_audio(self, pcm: bytes, sample_rate: int) -> None:
        """Tee inbound PCM16 bytes to BOTH backend + VAD.

        First call spawns the activity-event drainer tasks (they idle on
        an empty queue otherwise, so spawning per-call would waste a task
        per frame).
        """
        if self._closed:
            return
        self._ensure_drainers_running()
        # Tee — both sources receive the same audio bytes verbatim. The
        # backend forwards to the provider WebSocket; the VAD reframes
        # into 512-sample windows for ONNX inference.
        await asyncio.gather(
            self._backend.push_audio(pcm, sample_rate),
            self._vad.push_audio(pcm, sample_rate),
        )

    def transcripts(self) -> AsyncIterator[Transcript]:
        """Forward the backend's transcript stream verbatim.

        The Protocol returns a plain async iterator (D-02-5 precedent —
        ``def -> AsyncIterator``, NOT ``async def``). Consumers iterate
        with ``async for`` directly.
        """
        return self._backend.transcripts()

    async def close(self) -> None:
        """Cancel drainers + close both sources (idempotent)."""
        if self._closed:
            return
        self._closed = True
        for task in (self._vad_drain_task, self._provider_drain_task):
            if task is not None and not task.done():
                task.cancel()
                # Swallow drainer cancellation + any teardown errors —
                # close() must be idempotent + best-effort.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._vad_drain_task = None
        self._provider_drain_task = None
        await asyncio.gather(
            self._backend.close(),
            self._vad.close(),
            return_exceptions=True,
        )

    # ----- internal drainers --------------------------------------------

    def _ensure_drainers_running(self) -> None:
        """Spawn drainer tasks on first push_audio (idempotent)."""
        if self._vad_drain_task is None or self._vad_drain_task.done():
            self._vad_drain_task = asyncio.create_task(
                self._drain_vad_activity(),
                name="v2-seam-drain-vad-activity",
            )
        if self._provider_drain_task is None or self._provider_drain_task.done():
            self._provider_drain_task = asyncio.create_task(
                self._drain_provider_activity(),
                name="v2-seam-drain-provider-activity",
            )

    async def _drain_vad_activity(self) -> None:
        """Pull Silero VAD events + dispatch to listener (authoritative)."""
        async for event in self._vad.speech_activity_events():
            await self._dispatch(event)

    async def _drain_provider_activity(self) -> None:
        """Pull provider endpointing events + dispatch as corroborators.

        Per D-V2-2 + R-V2-2 combination_design: provider activity events
        are CORROBORATORS only. The seam adapter re-stamps them with
        ``corroborates=True`` so V4 can weight provider-confirmed
        endpoints higher without depending on the provider signal for
        sensor function.
        """
        async for event in self._backend.speech_activity_events():
            await self._dispatch(self._mark_corroborator(event))

    @staticmethod
    def _mark_corroborator(
        event: SpeechStartedEvent | SpeechEndedEvent,
    ) -> SpeechStartedEvent | SpeechEndedEvent:
        """Stamp provider-sourced events with ``corroborates=True``."""
        # Boundary records are frozen — model_copy with update is the
        # idiomatic Pydantic v2 mutation path.
        if event.event_type == "speech_ended":
            return event.model_copy(update={"corroborates": True})
        return event  # speech_started corroborators not consumed in v0.1

    async def _dispatch(
        self,
        event: SpeechStartedEvent | SpeechEndedEvent,
    ) -> None:
        """Dispatch one event to the listener if registered."""
        if self._listener is None:
            return
        # Use the discriminator field to narrow the union type for the listener
        # dispatch (frozen-Pydantic Literal field acts as a discriminated union).
        from persona_voice.stt.types import SpeechStartedEvent

        if isinstance(event, SpeechStartedEvent):
            await self._listener.on_speech_started(event)
        else:
            await self._listener.on_speech_ended(event)
