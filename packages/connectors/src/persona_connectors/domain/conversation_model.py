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
    "ChannelRef",
    "ConversationStateStore",
    "FlipPlan",
    "ForegroundRef",
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


class ChannelRef(BaseModel):
    """A connector channel a conversation belongs to (Spec C2 GAP-A, D-C2-X-gap-a-resolve-channel).

    The reverse of the routing slot: given a Persona ``conversation_id``, *which*
    platform channel does it live on? A C0-originated message carries only the
    internal ``conversation_id`` (:class:`~persona.schema.origination.OriginatedMessage`),
    but a connector's outbound (:class:`~persona_connectors.domain.normalise.NormalisedOutbound`)
    needs the platform ``conversation_key`` to deliver — this value type bridges
    that gap, returned by :meth:`ConversationStateStore.resolve_channel`.

    Surfaced co-developing C2 (the first real adapter) against C1: the framework
    owns the ``connector_conversations`` mapping, so the reverse lookup belongs in
    the framework, not hand-rolled in each adapter (criterion 10 / C2-R-3).

    Attributes:
        platform: The opaque platform key (e.g. ``"telegram"``) — the deliverer key.
        channel_key: The platform conversation key to deliver to (e.g. a Telegram
            ``chat.id``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: str
    channel_key: str


class ForegroundRef(BaseModel):
    """The persona currently in the foreground of a channel + its live conversation.

    The read primitive the inbound flow needs for the **no-name / continuation**
    case (Spec C2 T-flow, gated with :class:`ChannelRef`): a message with no persona
    name routes to the *active* persona (C1-D-4 sticky pointer), so the flow must
    read who that is. CQS-clean: a read, symmetric with
    :meth:`ConversationStateStore.resolve_channel` — the routing *decision*
    (:func:`~persona_connectors.domain.routing.decide_route`) and the foreground
    *flip* (:meth:`ConversationStateStore.foreground`) stay separate.

    Attributes:
        persona_id: The channel's currently-active (foregrounded) persona.
        conversation_id: That persona's live conversation to drive the turn on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona_id: str
    conversation_id: str


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

    def apply_new(self, *, owner_id: str, platform: str, channel_key: str) -> str | None:
        """``/new``: end the active persona's conversation and start a fresh one (§3).

        Per-persona-per-channel — only the *active* persona's slot is reset; the old
        conversation persists as history. Returns the new conversation id, or
        ``None`` when no persona is active. Owner-scoped + pointer-locked. (Declared
        on the port so the inbound flow's ``/new`` command can call it through the
        :class:`ConversationStateStore` abstraction — the concrete impl predates the
        flow.)
        """
        ...

    def current_foreground(
        self, *, owner_id: str, platform: str, channel_key: str
    ) -> ForegroundRef | None:
        """Read the channel's active persona + its live conversation, or ``None``.

        The no-name / continuation read: a message that names no persona routes to
        the active persona (C1-D-4), so the flow reads who that is and which
        conversation to continue. Returns ``None`` when no persona is foregrounded
        (the channel's pointer is unset) — the flow then auto-foregrounds a sole
        persona or replies with the list-and-instructions (C1-D-7). Owner-scoped
        (RLS, like :meth:`resolve_channel`). A read (CQS) — never mutates.
        """
        ...

    def resolve_channel(self, *, conversation_id: str) -> ChannelRef | None:
        """Resolve which connector channel a ``conversation_id`` belongs to (GAP-A).

        The reverse of :meth:`foreground`: maps a Persona ``conversation_id`` back
        to its :class:`ChannelRef` ``(platform, channel_key)`` so a C0-originated
        message (which carries only the ``conversation_id``) can be lowered to a
        :class:`~persona_connectors.domain.normalise.NormalisedOutbound` and sent.
        Returns ``None`` when the conversation has no connector channel (e.g. a
        web-only conversation) — the deliverer maps that to ``pending`` (never a
        silent drop, D-C1-X-platform-rejection).

        **Owner-scoping is the caller's responsibility** (the RLS-through-connector
        pattern, D-C1-X-rls-spine): this is a read scoped by the ambient
        ``current_user_id`` GUC, so the caller sets the owner scope (from the
        originated message's owner) BEFORE calling. It fails closed — no scope (or a
        different owner's scope) → no rows → ``None``, never a cross-tenant leak.
        """
        ...
