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

**Spec V8 cost gate (D-V8-1 — the split-tee).** :meth:`push_audio` splits
its tee through an optional :class:`~persona_voice.stt.protocol.StreamGate`:
the VAD ALWAYS receives every frame (barge-in onset must never be starved —
acceptance criterion #3), while the *billed* backend receives a frame only
when the gate is open. An absent gate (``None``, the default) is permanently
open — the pre-V8 ungated behaviour, so every V1–V6 path is unchanged until
a gate is wired. The deliverable-#1 policy (gate closed while the persona is
speaking) is a concrete gate supplied at composition time (T2).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona_voice.loop.streaming import Transcript
    from persona_voice.stt.protocol import (
        SpeechActivityListener,
        StreamGate,
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
        gate: StreamGate | None = None,
        reopen_preroll_ms: float = 0.0,
    ) -> None:
        """Wire the backend + VAD + optional listener + optional cost gate.

        Args:
            backend: Concrete :class:`StreamingSTT` (Deepgram at v0.1; the
                alternative-provider story per D-V2-1 swaps Speechmatics
                behind the same Protocol seam).
            vad: :class:`SileroVADAdapter` providing the AUTHORITATIVE
                onset + PRIMARY offset stream.
            listener: Optional :class:`SpeechActivityListener` consumer.
                If ``None`` at construction, set via :attr:`listener` later
                (T07 wiring path).
            gate: Optional Spec V8 :class:`StreamGate` (D-V8-1). Governs
                ONLY the billed-backend leg of the tee; the VAD always
                receives every frame. ``None`` (the default) is permanently
                open — the pre-V8 ungated behaviour. The shipped policy is
                wired post-construction via :attr:`gate`.
            reopen_preroll_ms: Spec V8 ring-buffer-on-reopen depth
                (D-V8-X-measure-stop-verdict). While the gate is closed, the
                most recent ``reopen_preroll_ms`` of inbound audio is buffered;
                on the next closed→open transition it is flushed to the backend
                ahead of the live frame, so the audio captured just before
                reopen (the barge-in confirm window / the idle-resume onset lag)
                reaches the provider and the first word is not clipped. ``0.0``
                (the default) disables the ring — the pre-ring behaviour, so
                every existing call site is unchanged.
        """
        self._backend = backend
        self._vad = vad
        self._listener: SpeechActivityListener | None = listener
        self._gate: StreamGate | None = gate
        self._vad_drain_task: asyncio.Task[None] | None = None
        self._provider_drain_task: asyncio.Task[None] | None = None
        self._closed = False
        # Spec V8 D-V8-X-cost-rebase — the cost instrument. Accumulates the
        # audio-seconds actually forwarded to the BILLED backend (post-gate),
        # which is what Deepgram charges for. Read at session end into
        # VoiceLog.stt_streamed_seconds to re-base stt_total_cents off real
        # streamed audio rather than wall-clock call duration.
        self._streamed_seconds: float = 0.0
        # Spec V8 ring-buffer-on-reopen (D-V8-X-measure-stop-verdict). The ring
        # fills ONLY during gated (closed) windows; on reopen its capped tail is
        # flushed ahead of the live frame, then cleared. Capacity is derived at
        # the D-V1-6 inbound invariant (PCM16 mono 16 kHz ⇒ 2 bytes/sample).
        self._reopen_preroll_ms = reopen_preroll_ms
        self._ring: deque[tuple[bytes, int]] = deque()
        self._ring_bytes = 0
        self._ring_capacity_bytes = int(reopen_preroll_ms / 1000.0 * 16_000) * 2
        # First frame is not treated as a reopen (the stream is conceptually
        # open at start; an opening gated window flips this to False).
        self._gate_was_open = True

    @property
    def listener(self) -> SpeechActivityListener | None:
        return self._listener

    @listener.setter
    def listener(self, value: SpeechActivityListener | None) -> None:
        self._listener = value

    @property
    def gate(self) -> StreamGate | None:
        """Spec V8 cost gate (D-V8-1), if any.

        The composition root (runner) sets this after construction so the
        gate↔orchestrator pair can be built without a chicken-and-egg (the
        gate reads orchestrator state, which is itself loop-backed). ``None``
        ⇒ permanently open (pre-V8 ungated behaviour).
        """
        return self._gate

    @gate.setter
    def gate(self, value: StreamGate | None) -> None:
        self._gate = value

    @property
    def streamed_seconds(self) -> float:
        """Audio-seconds forwarded to the billed backend so far (D-V8-X-cost-rebase).

        The cost basis: Deepgram bills per streamed second, and the split-tee
        only forwards a frame to the backend when the gate is open — so this
        counts exactly the billed audio. Read at session end into
        :attr:`persona_voice.logging.VoiceLog.stt_streamed_seconds` to re-base
        ``stt_total_cents`` off real streamed audio, not wall-clock duration.
        """
        return self._streamed_seconds

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
        # D-V8-1 split-tee. The VAD ALWAYS receives every frame — barge-in
        # onset is detected locally (free) and must never be starved, else
        # the user can't re-take the floor while gated (acceptance #3). The
        # billed backend receives a frame only when the StreamGate is open;
        # ``gate is None`` ⇒ permanently open (pre-V8 ungated behaviour).
        now_open = self._gate is None or self._gate.is_open()
        backend_frames = self._resolve_backend_frames(pcm, sample_rate, now_open=now_open)
        self._gate_was_open = now_open
        # Count what the billed backend actually receives (D-V8-X-cost-rebase):
        # PCM16 mono ⇒ 2 bytes/sample; seconds = bytes / (2 * sample_rate).
        for f_pcm, f_sr in backend_frames:
            self._streamed_seconds += len(f_pcm) / (2 * f_sr)
        # VAD (current frame, always) concurrently with the in-order backend send.
        # The VAD reframes into 512-sample ONNX windows; the backend forwards
        # verbatim to the provider WebSocket — frame ORDER matters there, so the
        # (possibly multi-frame) backend send is sequential, never gathered.
        await asyncio.gather(
            self._vad.push_audio(pcm, sample_rate),
            self._send_to_backend(backend_frames),
        )

    def _resolve_backend_frames(
        self, pcm: bytes, sample_rate: int, *, now_open: bool
    ) -> list[tuple[bytes, int]]:
        """Decide which frames reach the billed backend this push (D-V8-1 + ring).

        Without the ring (``reopen_preroll_ms == 0``): the live frame iff open.
        With the ring: the ring fills ONLY while closed; on a closed→open
        transition its capped tail is flushed ahead of the live frame and then
        cleared (so already-streamed open-window frames are never re-sent), and
        while open the ring stays empty.
        """
        if self._reopen_preroll_ms <= 0:
            return [(pcm, sample_rate)] if now_open else []
        if now_open:
            if not self._gate_was_open:
                # Reopen: flush the gated-window pre-roll tail, then the live frame.
                frames = [*self._ring, (pcm, sample_rate)]
                self._ring.clear()
                self._ring_bytes = 0
                return frames
            return [(pcm, sample_rate)]
        # Closed: buffer the live frame as pre-roll (capped); do not bill it.
        self._ring.append((pcm, sample_rate))
        self._ring_bytes += len(pcm)
        while self._ring_bytes > self._ring_capacity_bytes and self._ring:
            old_pcm, _old_sr = self._ring.popleft()
            self._ring_bytes -= len(old_pcm)
        return []

    async def _send_to_backend(self, frames: list[tuple[bytes, int]]) -> None:
        """Forward frames to the provider IN ORDER (websocket order is load-bearing)."""
        for f_pcm, f_sr in frames:
            await self._backend.push_audio(f_pcm, f_sr)

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
