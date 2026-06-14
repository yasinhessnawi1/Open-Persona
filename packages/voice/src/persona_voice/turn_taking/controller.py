"""TurnTakingController — the endpointing decision policy (spec V4 T02).

The first of V4's two judgement surfaces: *was that the end of the user's
turn?* (the ``USER_SPEAKING → PROCESSING`` handoff). This module is a **pure
decision function** — sensor input in, a :class:`TurnDecision` out, no side
effects, no wall-clock reads (the caller injects ``now``). That keeps it
exhaustively unit-testable with simulated activity-signal sequences, which is
how the single-most-tuned number in voice (the silence threshold) gets tuned
against real conversation (R-V4-1).

The policy (D-V4-1 — the *enriched* heuristic, NOT pure silence-threshold,
which would fail acceptance criterion #2):

1. **Silence-duration threshold.** The user must have been silent for at
   least ``silence_threshold_ms`` after their last speech offset before the
   turn can end. The provider/Silero offset (:class:`SpeechEndedEvent`) marks
   the offset; ``now − offset`` is the elapsed silence.

2. **Endpoint-signal corroboration.** A provider-corroborated offset
   (``corroborates=True``) or a settled final transcript
   (``transcript_settled=True``) shortens the required wait to
   ``corroborated_silence_threshold_ms`` — the endpoint signal weights the
   decision higher (V2 contract; D-V4-1).

3. **The deterministic textual-completion gate.** If the latest settled
   transcript ends on a hold-list token — a trailing conjunction, preposition,
   or filler ("and", "but", "so", "because", "to", "um", …) — the user was
   *mid-thought*; the controller WAITS past the silence threshold. This is the
   endpointing analog of the backchannel list (D-V4-3) and is what buys
   criterion #2 (mid-thought patience) without a semantic model. If the
   heuristic proves structurally too eager/slow at the T10 operator pass, the
   LiveKit ``turn-detector`` (Qwen2.5-0.5B) lands behind this same seam (it
   returns an EOU probability this controller already knows how to weight) —
   SEQUENCING, not v0.2-deferral (D-V4-1).

All thresholds are constructor-injected with the v1 *starting* values as
defaults (D-V4-X-rubric-and-params-tuning-surface — these are tuning surfaces,
not locked constants).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime for Pydantic field validation
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from persona_voice.stt.types import SpeechEndedEvent

__all__ = [
    "DEFAULT_TURN_END_HOLD_TOKENS",
    "TurnDecision",
    "TurnTakingController",
    "TurnVerdict",
]


# The deterministic textual-completion hold-list (D-V4-1). A settled
# transcript ending on one of these signals an incomplete utterance — the user
# paused mid-thought, not at a turn boundary. Lower-cased, punctuation-
# stripped comparison. Kept deliberately small and high-precision: a token
# here causes the persona to WAIT, so a false member only makes the persona a
# touch slower (the less-disruptive failure, D-V4-6), never ruder. English v1;
# multilingual sets ride in with the broader language work.
DEFAULT_TURN_END_HOLD_TOKENS: frozenset[str] = frozenset(
    {
        # coordinating / subordinating conjunctions
        "and",
        "but",
        "or",
        "nor",
        "so",
        "yet",
        "because",
        "since",
        "although",
        "though",
        "while",
        "if",
        "unless",
        "whether",
        "as",
        # prepositions / articles that cannot end a complete thought
        "to",
        "of",
        "for",
        "with",
        "the",
        "a",
        "an",
        "my",
        "your",
        "their",
        # fillers / hesitation markers
        "um",
        "uh",
        "er",
        "hmm",
        "like",
        "well",
    }
)


class TurnVerdict(StrEnum):
    """The controller's verdict on whether the user's turn has ended."""

    END_TURN = "end_turn"
    """The user is done; the persona should respond now."""
    WAIT = "wait"
    """The user paused but is not done (or has not been silent long enough)."""


class TurnDecision(BaseModel):
    """The result of one endpointing evaluation (frozen boundary record).

    Carries the verdict plus the reason + the elapsed silence so the
    operator-pass evidence and latency attribution (T08) can explain *why*
    a turn ended when it did (the silence-wait is the V4-attributable slice,
    D-V4-X-eou-stamp-point).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: TurnVerdict
    reason: str
    silence_elapsed_ms: float


class TurnTakingController:
    """Pure endpointing-decision policy (D-V4-1). No side effects, no clock.

    Args:
        silence_threshold_ms: Required silence after offset before a turn can
            end with no endpoint corroboration (v1 starting value 700 ms;
            "the single most-tuned number in voice" — spec §8).
        corroborated_silence_threshold_ms: The shortened wait when the offset
            is provider-corroborated or the final transcript has settled
            (v1 starting value 300 ms).
        hold_tokens: The textual-completion hold-list; defaults to
            :data:`DEFAULT_TURN_END_HOLD_TOKENS`.
    """

    def __init__(
        self,
        *,
        silence_threshold_ms: float = 700.0,
        corroborated_silence_threshold_ms: float = 300.0,
        hold_tokens: frozenset[str] = DEFAULT_TURN_END_HOLD_TOKENS,
        conservative_degradation: bool = True,
    ) -> None:
        self._silence_threshold_ms = silence_threshold_ms
        self._corroborated_silence_threshold_ms = corroborated_silence_threshold_ms
        self._hold_tokens = hold_tokens
        self._conservative_degradation = conservative_degradation

    @property
    def silence_threshold_ms(self) -> float:
        """The uncorroborated silence threshold (the orchestrator times against it)."""
        return self._silence_threshold_ms

    @property
    def corroborated_silence_threshold_ms(self) -> float:
        """The shortened silence threshold for a corroborated/settled offset."""
        return self._corroborated_silence_threshold_ms

    def decide_turn_end(
        self,
        *,
        last_offset: SpeechEndedEvent | None,
        settled_text: str | None,
        now: datetime,
    ) -> TurnDecision:
        """Decide whether the user's turn has ended.

        Args:
            last_offset: The most recent speech-offset event for the current
                utterance, or ``None`` if the user has not yet paused (still
                speaking — always ``WAIT``).
            settled_text: The latest settled transcript text for the turn
                (used by the textual-completion gate), or ``None`` if no
                transcript has settled yet.
            now: The caller-injected current instant (the silence is measured
                as ``now − last_offset.ts_emit``).

        Returns:
            A :class:`TurnDecision`.
        """
        if last_offset is None:
            # The user has not paused — they are still speaking.
            return TurnDecision(
                verdict=TurnVerdict.WAIT,
                reason="no_offset_user_still_speaking",
                silence_elapsed_ms=0.0,
            )

        silence_elapsed_ms = (now - last_offset.ts_emit).total_seconds() * 1000.0

        # The textual-completion gate (D-V4-1): a mid-thought trailing token
        # holds the floor regardless of silence — the user is not done.
        if settled_text is not None and self._ends_on_hold_token(settled_text):
            return TurnDecision(
                verdict=TurnVerdict.WAIT,
                reason="mid_thought_hold_token",
                silence_elapsed_ms=silence_elapsed_ms,
            )

        corroborated = last_offset.corroborates or last_offset.transcript_settled

        # Graceful-degradation bias (D-V4-6): an ambiguous endpoint — silence
        # with neither endpoint corroboration NOR any transcript to respond to —
        # is most likely a noise / false-VAD blip, not a real turn. Lean toward
        # the less-disruptive failure: wait rather than respond to a non-turn
        # (a slightly-long pause beats answering a cough). A corroborated offset
        # is trusted even with thin text.
        if self._conservative_degradation and not corroborated and not (settled_text or "").strip():
            return TurnDecision(
                verdict=TurnVerdict.WAIT,
                reason="ambiguous_no_transcript",
                silence_elapsed_ms=silence_elapsed_ms,
            )

        threshold = (
            self._corroborated_silence_threshold_ms if corroborated else self._silence_threshold_ms
        )

        if silence_elapsed_ms >= threshold:
            return TurnDecision(
                verdict=TurnVerdict.END_TURN,
                reason="corroborated_silence" if corroborated else "silence_threshold",
                silence_elapsed_ms=silence_elapsed_ms,
            )

        return TurnDecision(
            verdict=TurnVerdict.WAIT,
            reason="silence_below_threshold",
            silence_elapsed_ms=silence_elapsed_ms,
        )

    def _ends_on_hold_token(self, text: str) -> bool:
        """Whether ``text``'s final word is a mid-thought hold-list token."""
        # Strip trailing punctuation/whitespace, take the last whitespace-
        # delimited token, lower-case it. Empty text never holds.
        stripped = text.strip().rstrip(".,;:!?…-—\"'")
        if not stripped:
            return False
        last_word = stripped.split()[-1].lower().strip(".,;:!?…-—\"'")
        return last_word in self._hold_tokens
