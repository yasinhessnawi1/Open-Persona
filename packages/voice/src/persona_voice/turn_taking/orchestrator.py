"""ConversationalOrchestrator — the state-machine driver (spec V4 T04).

This is where the pure decision policies (T02 controller, T03 detector) and
the state vocabulary (T01) become a live conversation. The orchestrator:

* holds the current :class:`ConversationalState`;
* consumes V2's speech-activity signal (it IS a
  :class:`persona_voice.stt.protocol.SpeechActivityListener`) and V2's
  transcripts;
* runs the two judgement surfaces (turn-taking + barge-in) at the right
  moments and drives the guarded state transitions;
* performs the turn actions (invoke / cancel / interrupt) through an injected
  :class:`TurnActions` seam (T06 backs it with the V1 ``StreamingLoop``);
* broadcasts every transition on a :class:`ConversationalStateListener` seam
  (V6 renders it);
* owns the agent-speaking mute-window provider V2's VAD adapter consumes
  (D-V2-X-echo-cancellation) — :meth:`is_agent_speaking`.

**Timing is injected, not wall-clock-bound.** The two time-delayed decisions
— turn-end (after the silence threshold) and barge-in confirmation (after the
confirm window) — are scheduled through a :class:`Scheduler` seam and timed
with an injected ``clock``. The default scheduler wraps ``asyncio``; tests
inject a deterministic fake. This keeps the orchestrator's wiring exhaustively
unit-testable (the spec's whole point — the parameters are tuned, so the
machinery around them must be testable without real time).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from persona_voice.loop.streaming import Transcript
from persona_voice.turn_taking.barge_in import BargeInDetector, BargeInVerdict
from persona_voice.turn_taking.controller import TurnTakingController, TurnVerdict
from persona_voice.turn_taking.states import (
    AgentState,
    ConversationalState,
    ConversationalTransition,
    TransitionTrigger,
    advance,
    agent_state_for,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from persona_voice.stt.types import SpeechEndedEvent, SpeechStartedEvent

__all__ = [
    "AsyncioScheduler",
    "ConversationalOrchestrator",
    "ConversationalStateListener",
    "Scheduler",
    "SchedulerHandle",
    "TurnActions",
]


@runtime_checkable
class ConversationalStateListener(Protocol):
    """V6 consumer seam — notified on every conversational-state transition.

    Async so the consumer (e.g. V6's state-broadcast over the data channel)
    can do its own I/O without blocking the orchestrator. Implementations
    MUST NOT raise — a listener exception would corrupt the turn cycle.
    """

    async def on_state_changed(self, transition: ConversationalTransition) -> None: ...


@runtime_checkable
class TurnActions(Protocol):
    """The effects the orchestrator performs on the loop (T06 wires to V1).

    The orchestrator owns the *decisions*; this seam owns the *mechanism* —
    keeping the orchestrator testable in isolation and the V1 ``StreamingLoop``
    edit minimal (D-V4-X-t05-orchestrator-default).
    """

    async def invoke_model_for_turn(self, final_transcript: Transcript) -> None:
        """Start the V5→V3 generation for a completed user turn (PROCESSING)."""
        ...

    async def cancel_generation(self) -> None:
        """Cancel an in-flight generation that produced no audio yet (D-V4-5)."""
        ...

    async def interrupt(self) -> None:
        """Barge-in stop — cancel TTS + model + clear the outbound rail (D-V4-2)."""
        ...


@runtime_checkable
class SchedulerHandle(Protocol):
    """A cancellable scheduled callback."""

    def cancel(self) -> None: ...


@runtime_checkable
class Scheduler(Protocol):
    """Schedules a delayed async callback (the orchestrator's only timing dep)."""

    def call_later(
        self, delay_s: float, callback: Callable[[], Awaitable[None]]
    ) -> SchedulerHandle: ...


class _AsyncioHandle:
    """Default :class:`SchedulerHandle` — wraps an asyncio task."""

    def __init__(self, task: asyncio.Task[None]) -> None:
        self._task = task

    def cancel(self) -> None:
        if not self._task.done():
            self._task.cancel()


class AsyncioScheduler:
    """Default :class:`Scheduler` — ``sleep`` then run, on the running loop."""

    def call_later(
        self, delay_s: float, callback: Callable[[], Awaitable[None]]
    ) -> SchedulerHandle:
        task = asyncio.create_task(self._run(delay_s, callback))
        return _AsyncioHandle(task)

    async def _run(self, delay_s: float, callback: Callable[[], Awaitable[None]]) -> None:
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            return
        await callback()


class ConversationalOrchestrator:
    """Drives the four-state conversational machine (spec V4 §4).

    Args:
        actions: The loop-effect seam (invoke / cancel / interrupt).
        listener: Optional V6 state-broadcast consumer.
        controller: The endpointing policy (T02); a default is built if omitted.
        detector: The barge-in policy (T03); a default is built if omitted.
        scheduler: The timing seam (T04 default :class:`AsyncioScheduler`).
        clock: UTC-now provider (injected for deterministic tests).
    """

    def __init__(
        self,
        *,
        actions: TurnActions,
        listener: ConversationalStateListener | None = None,
        controller: TurnTakingController | None = None,
        detector: BargeInDetector | None = None,
        scheduler: Scheduler | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._actions = actions
        self._listener = listener
        self._controller = controller or TurnTakingController()
        self._detector = detector or BargeInDetector()
        self._scheduler = scheduler or AsyncioScheduler()
        self._clock = clock or (lambda: datetime.now(UTC))

        self._state = ConversationalState.LISTENING
        # Per-turn accumulation.
        self._latest_text: str | None = None
        self._final_transcript: Transcript | None = None
        self._last_offset: SpeechEndedEvent | None = None
        # Timer handles.
        self._turn_end_handle: SchedulerHandle | None = None
        self._barge_in_handle: SchedulerHandle | None = None
        # Barge-in candidate tracking.
        self._barge_in_onset: SpeechStartedEvent | None = None
        self._barge_in_resolved = False
        # The silence-wait of the most recent turn-end (T08 dual-line latency,
        # D-V4-X-eou-stamp-point) — the V4-attributable threshold cost, surfaced
        # separately from the processing round-trip.
        self._last_endpoint_silence_wait_ms: float | None = None

    # ----- inspection --------------------------------------------------

    @property
    def state(self) -> ConversationalState:
        return self._state

    @property
    def agent_state(self) -> AgentState:
        return agent_state_for(self._state)

    @property
    def last_endpoint_silence_wait_ms(self) -> float | None:
        """Silence-wait of the most recent turn-end (T08 dual-line latency).

        Feeds :func:`persona_voice.turn_taking.latency.attribute_hops` as the
        ``endpoint_silence_wait_ms`` line — the tuned-threshold cost surfaced
        separately from the processing round-trip (D-V4-X-eou-stamp-point).
        """
        return self._last_endpoint_silence_wait_ms

    def is_agent_speaking(self) -> bool:
        """The mute-window provider V2's VAD adapter consumes (D-V2-X).

        While the persona speaks, the user's inbound VAD must be gated so the
        persona's own voice does not fire a false barge-in. This is the single
        source of truth for ``AgentState.SPEAKING``.
        """
        return agent_state_for(self._state) is AgentState.SPEAKING

    # ----- V2 SpeechActivityListener -----------------------------------

    async def on_speech_started(self, event: SpeechStartedEvent) -> None:
        """Speech-onset — dispatched by state (D-V4-2/D-V4-5)."""
        if self._state is ConversationalState.LISTENING:
            self._reset_turn()
            await self._transition(TransitionTrigger.USER_SPEECH_STARTED)
        elif self._state is ConversationalState.USER_SPEAKING:
            # The user resumed after a brief pause — that offset was a
            # mid-thought pause, not a turn end. Cancel the pending turn-end.
            self._last_offset = None
            self._cancel_turn_end()
        elif self._state is ConversationalState.PROCESSING:
            # D-V4-5 — the user added more before the persona spoke: a
            # continuation. Cancel the (audio-less) generation and re-open the
            # turn, keeping the accumulated transcript.
            await self._actions.cancel_generation()
            # cancel_generation() awaited — a concurrent speech-activity source
            # (provider + VAD drains run as separate tasks) can have raced the
            # floor past PROCESSING in the meantime (e.g. its own continuation
            # already fired PROCESSING→USER_SPEAKING). Only fire the transition
            # if we still hold PROCESSING; otherwise the continuation is already
            # reflected and firing USER_CONTINUATION here is an illegal
            # user_speaking→user_continuation move.
            if self._state is not ConversationalState.PROCESSING:
                return
            self._last_offset = None
            await self._transition(TransitionTrigger.USER_CONTINUATION)
        elif self._state is ConversationalState.PERSONA_SPEAKING:
            # A potential barge-in — confirm it over the window (D-V4-2).
            self._barge_in_onset = event
            self._barge_in_resolved = False
            self._cancel_barge_in()
            self._barge_in_handle = self._scheduler.call_later(
                self._detector_confirm_window_s(), self._on_barge_in_confirm
            )

    async def on_speech_ended(self, event: SpeechEndedEvent) -> None:
        """Speech-offset — arm turn-end, or resolve a pending barge-in."""
        if self._state is ConversationalState.USER_SPEAKING:
            self._last_offset = event
            self._cancel_turn_end()
            self._turn_end_handle = self._scheduler.call_later(
                self._turn_end_delay_s(event), self._on_turn_end_timer
            )
        elif self._state is ConversationalState.PERSONA_SPEAKING:
            await self._resolve_barge_in_on_offset(event)

    # ----- V2 transcripts ----------------------------------------------

    async def on_transcript(self, transcript: Transcript) -> None:
        """Accumulate the turn's transcript (latest text + last final)."""
        self._latest_text = transcript.text
        if transcript.is_final:
            self._final_transcript = transcript

    # ----- loop callbacks (T06 wires these) ----------------------------

    async def notify_model_first_audio(self) -> None:
        """First persona audio reached the rail → PERSONA_SPEAKING."""
        if self._state is ConversationalState.PROCESSING:
            await self._transition(TransitionTrigger.MODEL_FIRST_AUDIO)

    async def notify_persona_finished(self) -> None:
        """Persona finished its reply normally → back to LISTENING."""
        if self._state is ConversationalState.PERSONA_SPEAKING:
            await self._transition(TransitionTrigger.PERSONA_FINISHED)

    async def notify_processing_yielded_no_audio(self) -> None:
        """Generation ended without audio (empty/error) → RESET to LISTENING."""
        if self._state is ConversationalState.PROCESSING:
            await self._transition(TransitionTrigger.RESET)

    async def force_reset(self) -> None:
        """Force the floor back to the user (RESET → LISTENING) — the recovery path.

        The graceful-degradation / watchdog escape (D-V4-6 / D-V4-X-watchdog-
        timeout): when a hop is stuck (e.g. the barge-in cancel chain hangs past
        the watchdog) the orchestrator forces the machine back to a clean
        LISTENING state rather than leaving the call wedged. A no-op if already
        LISTENING (RESET from LISTENING is not a legal transition).
        """
        if self._state is ConversationalState.LISTENING:
            return
        self._cancel_turn_end()
        self._cancel_barge_in()
        self._reset_turn()
        await self._transition(TransitionTrigger.RESET)

    # ----- turn-end timer ----------------------------------------------

    async def _on_turn_end_timer(self) -> None:
        if self._state is not ConversationalState.USER_SPEAKING:
            return
        decision = self._controller.decide_turn_end(
            last_offset=self._last_offset,
            settled_text=self._latest_text,
            now=self._clock(),
        )
        if decision.verdict is TurnVerdict.END_TURN:
            # Record the silence-wait as the V4-attributable latency line (T08).
            self._last_endpoint_silence_wait_ms = decision.silence_elapsed_ms
            await self._transition(TransitionTrigger.TURN_ENDED)
            await self._actions.invoke_model_for_turn(self._turn_transcript())
        # WAIT: leave the floor with the user; graceful-degradation bounding
        # of a stuck hold-token is T09's concern.

    # ----- barge-in confirmation ---------------------------------------

    async def _on_barge_in_confirm(self) -> None:
        if (
            self._state is not ConversationalState.PERSONA_SPEAKING
            or self._barge_in_resolved
            or self._barge_in_onset is None
        ):
            return
        sustained_ms = self._elapsed_ms(self._barge_in_onset.ts_emit, self._clock())
        decision = self._detector.decide_barge_in(
            onset=self._barge_in_onset,
            agent_state=self.agent_state,
            sustained_ms=sustained_ms,
            ended=False,
        )
        if decision.verdict is BargeInVerdict.INTERRUPT:
            await self._do_barge_in()

    async def _resolve_barge_in_on_offset(self, offset: SpeechEndedEvent) -> None:
        if self._barge_in_onset is None or self._barge_in_resolved:
            return
        self._cancel_barge_in()
        sustained_ms = self._elapsed_ms(self._barge_in_onset.ts_emit, offset.ts_emit)
        decision = self._detector.decide_barge_in(
            onset=self._barge_in_onset,
            agent_state=self.agent_state,
            sustained_ms=sustained_ms,
            ended=True,
        )
        if decision.verdict is BargeInVerdict.INTERRUPT:
            await self._do_barge_in()
        else:
            # Backchannel / blip — keep the floor with the persona.
            self._barge_in_resolved = True

    async def _do_barge_in(self) -> None:
        """Yield the floor at once: interrupt the loop, then transition."""
        self._barge_in_resolved = True
        self._cancel_barge_in()
        # Effect first (stop the audio + cancel the stale reply), then move the
        # machine — so the mute window opens only after the rail is quiet.
        await self._actions.interrupt()
        self._reset_turn()
        await self._transition(TransitionTrigger.BARGE_IN)

    # ----- transition + helpers ----------------------------------------

    async def _transition(self, trigger: TransitionTrigger) -> None:
        new_state = advance(self._state, trigger)
        if new_state == self._state:
            return
        old_state = self._state
        self._state = new_state
        if self._listener is not None:
            await self._listener.on_state_changed(
                ConversationalTransition(
                    from_state=old_state,
                    to_state=new_state,
                    trigger=trigger,
                    at=self._clock(),
                )
            )

    def _turn_transcript(self) -> Transcript:
        """The transcript handed to the model — the last final, or a synthesised
        one from the latest text if no final settled (robust fallback)."""
        if self._final_transcript is not None:
            return self._final_transcript
        return Transcript(is_final=True, text=self._latest_text or "", confidence=1.0)

    def _reset_turn(self) -> None:
        self._latest_text = None
        self._final_transcript = None
        self._last_offset = None
        self._cancel_turn_end()

    def _cancel_turn_end(self) -> None:
        if self._turn_end_handle is not None:
            self._turn_end_handle.cancel()
            self._turn_end_handle = None

    def _cancel_barge_in(self) -> None:
        if self._barge_in_handle is not None:
            self._barge_in_handle.cancel()
            self._barge_in_handle = None

    def _turn_end_delay_s(self, offset: SpeechEndedEvent) -> float:
        corroborated = offset.corroborates or offset.transcript_settled
        ms = (
            self._controller.corroborated_silence_threshold_ms
            if corroborated
            else self._controller.silence_threshold_ms
        )
        return ms / 1000.0

    def _detector_confirm_window_s(self) -> float:
        return self._detector.confirm_window_ms / 1000.0

    @staticmethod
    def _elapsed_ms(start: datetime, end: datetime) -> float:
        return (end - start).total_seconds() * 1000.0
