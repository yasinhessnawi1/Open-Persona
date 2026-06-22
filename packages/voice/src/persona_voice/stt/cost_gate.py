"""Spec V8 cost gates — concrete :class:`StreamGate` policies (D-V8-2).

The split-tee (D-V8-1, :mod:`persona_voice.stt.seam_adapter`) governs the
*billed* STT leg through a :class:`~persona_voice.stt.protocol.StreamGate`
predicate while the Silero VAD is always fed. This module supplies the
concrete gate policies that decide *when* the billed leg is open.

**Deliverable #1 — :class:`PersonaSpeakingGate`.** The cheap, high-value,
low-clipping-risk cut: do NOT stream to the provider while the orchestrator
is ``PERSONA_SPEAKING``. The user is not speaking then, so nothing is
clipped; barge-in is detected locally by Silero (still fed by the split-tee),
so the moment the user takes the floor the orchestrator leaves
``PERSONA_SPEAKING`` and the gate reopens — KeepAlive held the socket open
meanwhile, so the next turn re-opens with no reconnect (criterion #5).

**The read seam — :class:`AgentSpeakingSource`.** A minimal, read-only
Protocol exposing "is the persona currently speaking?" The
:class:`persona_voice.turn_taking.orchestrator.ConversationalOrchestrator`
satisfies it structurally (``is_agent_speaking()`` is ``True`` iff
``ConversationalState.PERSONA_SPEAKING``). Defined here, in the STT package,
so the gate depends on this minimal surface without importing the turn-taking
subpackage — turn-taking already imports STT (`stt.types`), so the reverse
import would cycle. The orchestrator never imports this Protocol; the runner
composition root injects it (D-V4-X-t05-orchestrator-default — additive,
default ``None`` ⇒ permanently open ⇒ no behaviour change).

Deliverable #2's onset-gated policy (pre-roll + hold-over / Finalize) lands
here at T4, once the empirical grid fixes its constants (D-V8-X-t4-promotion-gate).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = [
    "DEFAULT_REOPEN_PREROLL_MS",
    "AgentSpeakingSource",
    "IdleAwareGate",
    "PersonaSpeakingGate",
    "UserTurnSource",
]

# Spec V8 ring-buffer-on-reopen depth (D-V8-X-measure-stop-verdict). Principled:
# the barge-in confirm window (~200 ms, BargeInDetector.confirm_window_ms) plus
# the Silero onset-detection lag (~50–90 ms) plus margin. The seam adapter buffers
# this much audio while gated and flushes it on reopen so the barge-in opening /
# first post-idle word reaches the provider. Constructor-injectable; the scoped
# live leg validates it against the D-V8-5 first-word=100% bar.
DEFAULT_REOPEN_PREROLL_MS: float = 300.0


@runtime_checkable
class AgentSpeakingSource(Protocol):
    """Read-only seam: does the persona currently hold the floor (is speaking)?

    The orchestrator's ``is_agent_speaking()`` satisfies this structurally —
    it returns ``True`` exactly while the conversational state is
    ``PERSONA_SPEAKING`` (``PROCESSING`` / ``PREPARING`` project to
    ``THINKING``, not ``SPEAKING``). A pure, cheap, non-blocking state read
    called once per inbound frame.
    """

    def is_agent_speaking(self) -> bool:
        """Return ``True`` iff the persona is currently speaking the reply."""


class PersonaSpeakingGate:
    """A :class:`StreamGate` that closes the billed leg while the persona speaks.

    Deliverable #1 (D-V8-2). ``is_open()`` is ``True`` (stream to the provider)
    whenever the persona is NOT speaking, and ``False`` (withhold the billed
    leg; the VAD is still fed by the split-tee) while the persona is speaking.

    ``source=None`` ⇒ permanently open — the additive default so a runner that
    has no orchestrator (the echo/dev baseline) sees no behaviour change. The
    runner binds the real source (the orchestrator) post-construction.
    """

    def __init__(self, source: AgentSpeakingSource | None = None) -> None:
        """Wire the speaking-state source (``None`` ⇒ permanently open)."""
        self._source = source

    @property
    def source(self) -> AgentSpeakingSource | None:
        """The bound :class:`AgentSpeakingSource`, if any."""
        return self._source

    @source.setter
    def source(self, value: AgentSpeakingSource | None) -> None:
        self._source = value

    def is_open(self) -> bool:
        """Open iff the persona is NOT speaking (closed during ``PERSONA_SPEAKING``)."""
        return self._source is None or not self._source.is_agent_speaking()


@runtime_checkable
class UserTurnSource(Protocol):
    """Read-only seam: is the user's turn in progress (speaking or being processed)?

    The orchestrator's ``is_user_turn_active()`` satisfies this structurally —
    ``True`` iff the conversational state is ``USER_SPEAKING`` or ``PROCESSING``.
    A pure, cheap, non-blocking state read.
    """

    def is_user_turn_active(self) -> bool:
        """Return ``True`` iff the user's turn is in progress."""


class IdleAwareGate:
    """A :class:`StreamGate` open ONLY during the user's turn (D-V8-X-measure-stop-verdict).

    The shipped Spec V8 production gate (idle-gate, ~85 % saving on a listen-heavy
    call). Open iff the user's turn is active (``USER_SPEAKING`` / ``PROCESSING``);
    closed during ``PERSONA_SPEAKING`` + ``LISTENING`` idle + ``PREPARING``. It
    **supersets** :class:`PersonaSpeakingGate` (#1): everything #1 withholds plus
    the between-turn idle. Lower-risk than the declined #2 within-utterance gating
    — it streams the *whole* user turn (never clips the user's own words mid-turn);
    only the reopen-into-the-turn needs a pre-roll, which the seam adapter's shared
    ring-buffer-on-reopen supplies (covering both this idle-resume AND barge-in).

    ``source=None`` ⇒ permanently open — the additive default (no behaviour change
    when unwired). The runner binds the orchestrator post-construction.
    """

    def __init__(self, source: UserTurnSource | None = None) -> None:
        """Wire the user-turn source (``None`` ⇒ permanently open)."""
        self._source = source

    @property
    def source(self) -> UserTurnSource | None:
        """The bound :class:`UserTurnSource`, if any."""
        return self._source

    @source.setter
    def source(self, value: UserTurnSource | None) -> None:
        self._source = value

    def is_open(self) -> bool:
        """Open iff the user's turn is active (closed during persona/idle/preparing)."""
        return self._source is None or self._source.is_user_turn_active()
