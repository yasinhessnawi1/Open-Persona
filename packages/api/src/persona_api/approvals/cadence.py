"""Cadence caps — priority-aware C0 outreach, chatter batches to the digest (Spec A3, T12; A3-D-4).

A busy persona must not become a spammer — but it must *always* be able to ask and to say it's
stuck. So the cap is **priority-classed**, not a blanket rate limit:

- **approval / failure / safety** → **always deliver**, unconditionally, never counted against
  the cap. This is load-bearing: the T10 "budget reached; extend?" message is an *approval*-class
  C0, so if the cap could ever suppress it a budget-halted task would become permanently
  un-extendable (deadlock). Approvals, failure accounts, and kill-switch confirmations bypass.
- **progress** (chatter) → capped per persona/day; over the cap it **batches to the digest** (the
  A6 morning review — A3 defines the :class:`DigestSink` seam + the priority classes; A6 stores +
  renders).

The per-persona/day chatter count rides one atomic conditional upsert (``ON CONFLICT … DO UPDATE
… WHERE count < cap``) so concurrent chatter can never exceed the cap. The gate returns
``DELIVER`` (originate the C0 now) or ``DIGEST`` (hand to the sink); the caller (the originator
composition) classifies each message and routes it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from persona.logging import get_logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from persona_api.db.engine import rls_connection
from persona_api.db.models import cadence_counters as counters_t

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Engine

__all__ = [
    "DEFAULT_DAILY_CAP",
    "CadenceDecision",
    "CadenceGate",
    "DigestSink",
    "MessagePriority",
    "bypasses_cap",
]

_log = get_logger("api.approvals.cadence")

#: Default per-persona/day chatter cap (A3-D-4); env-tunable (``PERSONA_CADENCE_DAILY_CAP``).
DEFAULT_DAILY_CAP = 10


class MessagePriority(StrEnum):
    """The C0 message class the cadence gate keys on.

    ``approval`` / ``failure`` / ``safety`` always pass (never capped, never counted);
    ``progress`` is the chatter the cap governs.
    """

    APPROVAL = "approval"
    FAILURE = "failure"
    SAFETY = "safety"
    PROGRESS = "progress"


#: The classes that bypass the cap unconditionally — the persona can always ask + always report.
_ALWAYS_PASS: frozenset[MessagePriority] = frozenset(
    {MessagePriority.APPROVAL, MessagePriority.FAILURE, MessagePriority.SAFETY}
)


def bypasses_cap(priority: MessagePriority) -> bool:
    """True iff ``priority`` always delivers regardless of the cadence cap (the bypass classes).

    The no-silent-failure guarantee (criterion 7) leans on this: every failure account is a
    cadence-bypass class, so a chatty persona's cap can never suppress a failure report.
    """
    return priority in _ALWAYS_PASS


class CadenceDecision(StrEnum):
    """What the gate decided for a message."""

    DELIVER = "deliver"
    DIGEST = "digest"


class DigestSink(Protocol):
    """Where over-cap chatter is batched until the A6 morning review (A6 implements the store)."""

    def defer(self, owner_id: str, persona_id: str, content: str, *, now: datetime) -> None: ...


class CadenceGate:
    """Decides DELIVER vs DIGEST for a C0 message, priority-classed (A3-D-4)."""

    def __init__(self, engine: Engine, *, daily_cap: int = DEFAULT_DAILY_CAP) -> None:
        self._engine = engine
        self._cap = daily_cap

    def admit(
        self,
        owner_id: str,
        persona_id: str,
        priority: MessagePriority,
        *,
        now: datetime,
    ) -> CadenceDecision:
        """DELIVER now, or DIGEST (over the chatter cap).

        Priority messages (approval / failure / safety) always DELIVER and never touch the
        counter. A progress message DELIVERs while under the cap (atomically incrementing the
        per-persona/day count) and DIGESTs once the cap is reached.
        """
        if priority in _ALWAYS_PASS:
            return CadenceDecision.DELIVER
        if self._cap <= 0:
            return CadenceDecision.DIGEST  # no chatter budget at all
        day = now.date()
        with rls_connection(self._engine, owner_id) as conn:
            row = (
                conn.execute(
                    pg_insert(counters_t)
                    .values(owner_id=owner_id, persona_id=persona_id, day=day, count=1)
                    .on_conflict_do_update(
                        constraint="pk_cadence_counters",
                        set_={"count": counters_t.c.count + 1},
                        where=(counters_t.c.count < self._cap),
                    )
                    .returning(counters_t.c.count)
                )
                .mappings()
                .first()
            )
        # A row → the insert/increment applied (under cap) → deliver. No row → the conditional
        # update's WHERE failed (count == cap) → the chatter batches to the digest.
        return CadenceDecision.DELIVER if row is not None else CadenceDecision.DIGEST
