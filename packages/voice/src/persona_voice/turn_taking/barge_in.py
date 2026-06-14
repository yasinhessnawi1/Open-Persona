"""BargeInDetector — the fast-and-discriminating onset judgement (spec V4 T03).

The second of V4's two judgement surfaces: *is that a genuine interruption?*
(the ``PERSONA_SPEAKING → USER_SPEAKING`` handoff). Like the controller, this
is a **pure decision function** — observation in, a :class:`BargeInDecision`
out, no side effects, no clock (the caller injects the timing) — so the
fast-vs-discriminating balance (R-V4-1, the spec's central tension) is tuned
against real conversation via constructor-injected thresholds.

The policy (D-V4-2 + D-V4-3):

* **Scope gate.** A barge-in only exists while the persona is speaking
  (``AgentState.SPEAKING``). An onset in any other state is not a barge-in.
* **Confidence/energy gate.** A Silero onset below ``min_onset_confidence``
  is background noise / TTS bleed-through, not a real interruption (V2 emits
  deliberately-high-false-positive onsets — the 51% raw-VAD FP rate — and
  names V4 the decider; D-V4-2).
* **Confirmation window (responsive path).** An onset still active after
  ``confirm_window_ms`` is a sustained interruption → ``INTERRUPT``. This is
  the ~200 ms class onset latency that makes barge-in feel instant.
* **Backchannel bar (discriminating path).** An onset that *ended* with a
  total duration below ``backchannel_bar_ms`` was a cough / "mhm" / "yeah" —
  a backchannel, not an interruption → ``IGNORE``. Humans don't stop for
  "uh-huh" (D-V4-3).

**The deliberate tension (spec §8 — "cannot be perfectly resolved").** The
confirm window is the barge-in *latency*; the backchannel bar is the
*discrimination* threshold. A backchannel longer than the confirm window but
shorter than the bar that is still ongoing at the confirm window will
interrupt — fast-and-discriminating hold "to an acceptable degree", not
perfectly. The orchestrator (T06) drives the timing: it evaluates at the
confirm window when speech is still active (→ responsive INTERRUPT) and on
the offset when speech ended (→ backchannel IGNORE). The two thresholds let
the operator slide the tradeoff (D-V4-X-rubric-and-params-tuning-surface).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from persona_voice.turn_taking.states import AgentState

if TYPE_CHECKING:
    from persona_voice.stt.types import SpeechStartedEvent

__all__ = [
    "BargeInDecision",
    "BargeInDetector",
    "BargeInVerdict",
]


class BargeInVerdict(StrEnum):
    """The detector's verdict on a speech-onset during persona speech."""

    INTERRUPT = "interrupt"
    """A genuine interruption — yield the floor at once (cancel + listen)."""
    IGNORE = "ignore"
    """Noise / backchannel / out-of-scope onset — keep the persona speaking."""
    PENDING = "pending"
    """Not yet enough evidence; the onset is live but unconfirmed — keep watching."""


class BargeInDecision(BaseModel):
    """The result of one barge-in evaluation (frozen boundary record)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: BargeInVerdict
    reason: str
    sustained_ms: float


class BargeInDetector:
    """Pure barge-in-decision policy (D-V4-2/3). No side effects, no clock.

    Args:
        confirm_window_ms: How long an onset must remain active to count as a
            sustained interruption (v1 starting value 200 ms — the responsive
            onset latency).
        backchannel_bar_ms: An onset that ended with total duration below this
            is treated as a backchannel/cough and ignored (v1 starting value
            350 ms).
        min_onset_confidence: The Silero onset-confidence floor; onsets below
            it are noise / TTS bleed-through (v1 starting value 0.6). Provider
            events with no confidence (``None``) bypass this gate.
    """

    def __init__(
        self,
        *,
        confirm_window_ms: float = 200.0,
        backchannel_bar_ms: float = 350.0,
        min_onset_confidence: float = 0.6,
    ) -> None:
        self._confirm_window_ms = confirm_window_ms
        self._backchannel_bar_ms = backchannel_bar_ms
        self._min_onset_confidence = min_onset_confidence

    @property
    def confirm_window_ms(self) -> float:
        """The onset confirmation window (the orchestrator times against it)."""
        return self._confirm_window_ms

    def decide_barge_in(
        self,
        *,
        onset: SpeechStartedEvent,
        agent_state: AgentState,
        sustained_ms: float,
        ended: bool,
    ) -> BargeInDecision:
        """Decide whether an onset during persona speech is a barge-in.

        Args:
            onset: The speech-onset event under evaluation.
            agent_state: The current persona-side state. Only
                :attr:`AgentState.SPEAKING` is in barge-in scope.
            sustained_ms: How long the onset has been active so far (the
                caller computes this from its clock — ``now − onset.ts_emit``,
                or ``offset − onset`` once the offset is observed).
            ended: Whether an offset has been observed for this onset (the
                user already stopped — used for the backchannel bar).

        Returns:
            A :class:`BargeInDecision`.
        """
        if agent_state is not AgentState.SPEAKING:
            return BargeInDecision(
                verdict=BargeInVerdict.IGNORE,
                reason="not_persona_speaking",
                sustained_ms=sustained_ms,
            )

        # Confidence/energy gate — only when the sensor reports confidence
        # (Silero onsets do; provider/synthetic onsets do not).
        if onset.confidence is not None and onset.confidence < self._min_onset_confidence:
            return BargeInDecision(
                verdict=BargeInVerdict.IGNORE,
                reason="below_confidence_gate",
                sustained_ms=sustained_ms,
            )

        if ended:
            # The user already stopped. A short utterance during persona
            # speech is a backchannel / cough / blip — do not yield the floor.
            if sustained_ms < self._backchannel_bar_ms:
                return BargeInDecision(
                    verdict=BargeInVerdict.IGNORE,
                    reason="backchannel_short_utterance",
                    sustained_ms=sustained_ms,
                )
            return BargeInDecision(
                verdict=BargeInVerdict.INTERRUPT,
                reason="completed_interruption",
                sustained_ms=sustained_ms,
            )

        # Still active: a sustained onset past the confirm window is a real,
        # responsive interruption.
        if sustained_ms >= self._confirm_window_ms:
            return BargeInDecision(
                verdict=BargeInVerdict.INTERRUPT,
                reason="sustained_onset",
                sustained_ms=sustained_ms,
            )

        return BargeInDecision(
            verdict=BargeInVerdict.PENDING,
            reason="awaiting_confirmation",
            sustained_ms=sustained_ms,
        )
