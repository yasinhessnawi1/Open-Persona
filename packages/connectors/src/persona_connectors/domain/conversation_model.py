"""The per-persona parallel-conversation model (Spec C1 T6, §3) — the agentic linchpin.

Each persona has at most one active conversation per user per channel; naming a
persona FOREGROUNDS it and SUSPENDS (never ends) the previously-active one — so a
persona's in-flight work survives the user glancing at another persona. This
module is the **pure decision + the port** half (the C0/T3 shape):

- :func:`decide_foreground` — the pure §3 rules over the current channel state
  (no-op re-name / suspend-and-switch / resume-or-start). Exhaustively unit-tested,
  no I/O.
- :class:`ConversationStateStore` — the persistence **Protocol**; the concrete
  adapter (``persona_connectors.infra``) performs the transactional ``SELECT … FOR
  UPDATE`` flip + suspend/resume persistence on the T5 tables.

The ``/new`` + idle-timeout boundary mechanics (ending a conversation) are a later
task; this owns foreground/suspend/resume/never-reset only. Owned surface —
api-free.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ConversationStateStore",
    "FlipPlan",
    "ForegroundResult",
    "NoOp",
    "Switch",
    "decide_foreground",
]


class NoOp(BaseModel):
    """Re-naming the already-active persona — nothing changes (the always-safe rule)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Switch(BaseModel):
    """Foreground a different persona: suspend the current (if any), resume-or-start the named.

    Attributes:
        suspend_persona_id: The previously-active persona to SUSPEND (not end);
            ``None`` when nothing was active.
        foreground_persona_id: The named persona to bring to the foreground.
        resume: ``True`` to resume the named persona's existing (suspended) slot —
            its conversation continues intact; ``False`` to start a fresh
            conversation for it (no live slot).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    suspend_persona_id: str | None
    foreground_persona_id: str
    resume: bool


# A foreground decision is either a no-op (re-name active) or a switch.
FlipPlan = NoOp | Switch


class ForegroundResult(BaseModel):
    """The outcome of a foreground operation — the conversation to drive the turn on.

    Attributes:
        conversation_id: The now-active persona's conversation (resumed or freshly
            started) — the conversation the runtime turn attaches to.
        resumed: ``True`` if an existing suspended conversation was resumed intact,
            ``False`` if a fresh one was started (observability; the relationship
            memory persists across boundaries regardless).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str
    resumed: bool


def decide_foreground(
    *, active_persona_id: str | None, named_persona_id: str, named_has_resumable_slot: bool
) -> FlipPlan:
    """Decide what foregrounding ``named_persona_id`` does, given the channel state (§3).

    Pure: re-naming the active persona is a :class:`NoOp` (never resets); otherwise
    a :class:`Switch` that suspends whoever was active (if anyone) and resumes the
    named persona's live slot or starts it fresh. The transactional application is
    the store adapter's job.
    """
    if named_persona_id == active_persona_id:
        return NoOp()
    return Switch(
        suspend_persona_id=active_persona_id,
        foreground_persona_id=named_persona_id,
        resume=named_has_resumable_slot,
    )


@runtime_checkable
class ConversationStateStore(Protocol):
    """The persistence port for the parallel-conversation model (the C0 recorder shape).

    The concrete adapter (``persona_connectors.infra``) implements
    :meth:`foreground` transactionally: it materialises + ``SELECT … FOR UPDATE``-locks
    the channel pointer row (serialising concurrent flips — D-C1-2), reads the
    current active persona + the named persona's slot, applies
    :func:`decide_foreground`, persists the suspend/resume/start + the pointer flip,
    and returns the now-active conversation. ``@runtime_checkable`` so a composition
    root can assert the injected store satisfies the port.
    """

    def foreground(
        self, *, owner_id: str, platform: str, channel_key: str, persona_id: str
    ) -> ForegroundResult:
        """Foreground ``persona_id`` on the channel and return its active conversation.

        Atomic: a no-op re-name returns the current conversation unchanged; a switch
        suspends the previously-active persona's conversation (never ends it) and
        resumes the named persona's suspended conversation intact, or starts a fresh
        one — then points the channel at the named persona.
        """
        ...
