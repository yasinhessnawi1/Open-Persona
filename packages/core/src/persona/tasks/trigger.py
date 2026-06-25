"""The resume-trigger seam — what wakes a leg, and the resume interface (Spec A2, T3; D-A2-5).

All three wait kinds converge on ONE resume mechanism: a :class:`TaskResumer` materialises
a resume leg (an A0 job) carrying the :class:`ResumeTrigger` into the next leg's
reconstruction. ``ScheduledFire`` (rides A1's schedule) and ``UserReply`` (a C0/C1 answer
resumes ``waiting(on_user)``) are exercised for real in v1; ``EventTrigger`` is the reserved
seam — the type exists and flows through reconstruction, but **no event source constructs
one in v1** (defined, implemented never — direction 5 fills it by calling the same
``resume_task``). One function-shaped seam, not a watch framework (mirrors
D-A1-X-no-trigger-framework).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from persona.tasks.state import WaitKind

__all__ = [
    "EventTrigger",
    "ResumeTrigger",
    "ScheduledFire",
    "TaskResumer",
    "UserReply",
    "wait_kind_for",
]


def _ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes; normalise tz-aware ones to UTC (house rule)."""
    if value.tzinfo is None:
        msg = "naive datetime not allowed; use datetime.now(UTC) or attach a tzinfo"
        raise ValueError(msg)
    return value.astimezone(UTC)


class ScheduledFire(BaseModel):
    """A self-scheduled continuation fired through A1 (``waiting(until_time)``).

    Carries the A1 handoff anchor (``schedule_id`` + ``fire_time``) so the resumed leg can
    deterministically answer "which morning is this" (D-A1-X-handoff-payload).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["scheduled_fire"] = "scheduled_fire"
    schedule_id: str
    fire_time: datetime

    @field_validator("fire_time", mode="after")
    @classmethod
    def _fire_time_tz_aware(cls, value: datetime) -> datetime:
        return _ensure_utc(value)


class UserReply(BaseModel):
    """A user's answer that resumes a ``waiting(on_user)`` task (C0 posed, C1 answered)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["user_reply"] = "user_reply"
    reply: str
    in_reply_to: str | None = None


class EventTrigger(BaseModel):
    """The reserved ``waiting(on_event)`` trigger — defined, NO producer in v1 (D-A2-5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["event"] = "event"
    source: str
    payload: str


#: A leg's trigger — a discriminated union over the three wait kinds (Pydantic keys on
#: ``kind``). The final element of context reconstruction (D-A2-3).
ResumeTrigger = Annotated[
    ScheduledFire | UserReply | EventTrigger,
    Field(discriminator="kind"),
]

_WAIT_KIND_BY_TRIGGER: dict[str, WaitKind] = {
    "scheduled_fire": WaitKind.UNTIL_TIME,
    "user_reply": WaitKind.ON_USER,
    "event": WaitKind.ON_EVENT,
}


def wait_kind_for(trigger: ScheduledFire | UserReply | EventTrigger) -> WaitKind:
    """Return the :class:`WaitKind` a ``trigger`` resolves (the wait it un-parks)."""
    return _WAIT_KIND_BY_TRIGGER[trigger.kind]


@runtime_checkable
class TaskResumer(Protocol):
    """The resume interface an event source (or the scheduler/reply path) calls (D-A2-5).

    The one function-shaped seam: materialise a resume leg for ``task_id`` carrying
    ``trigger``. Implemented in persona-api/runtime for the ``until_time`` + ``on_user``
    paths in v1; ``EventTrigger`` has no caller until direction 5 adds event sources.
    """

    def resume_task(
        self, task_id: str, trigger: ScheduledFire | UserReply | EventTrigger
    ) -> None: ...
