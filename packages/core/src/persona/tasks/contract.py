"""The task contract — the A4-authored anchor against drift (Spec A2, T2; D-A2-1).

Over many legs, small reinterpretations of the goal compound (the classic long-horizon
failure). The contract is the fixed anchor every leg re-reads: a frozen value type with
the goal, scope, acceptance criteria (statement + status), and stated bounds. It carries
**no mutation method** — a leg structurally cannot rewrite it. Acceptance-criterion
*status* advances through the Task (the controlled, status-only path), never by editing
the goal/scope/statements; a task that concludes the contract itself must change goes
``waiting(on_user)`` with A4's amendment proposal (spec §3), it does not improvise.

The cadence the task runs at (the contract's "schedule" aspect) is realised as the
task's ``schedule_id`` linkage to A1's durable schedule entity, not embedded here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from persona.tools.category_policy import (  # noqa: TC001 — Pydantic needs runtime access
    DEFAULT_POLICY,
    CategoryPolicy,
)

__all__ = [
    "AcceptanceCriterion",
    "AcceptanceStatus",
    "Contract",
    "ContractBounds",
]


def _ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes; normalise tz-aware ones to UTC (house rule)."""
    if value.tzinfo is None:
        msg = "naive datetime not allowed; use datetime.now(UTC) or attach a tzinfo"
        raise ValueError(msg)
    return value.astimezone(UTC)


class AcceptanceStatus(StrEnum):
    """The pass/fail status of one acceptance criterion (drives pick-highest-not-done)."""

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class AcceptanceCriterion(BaseModel):
    """One acceptance criterion: an immutable statement + a mutable-via-Task status.

    The ``statement`` and ``id`` are part of the A4-authored anchor (a leg cannot
    change them); the ``status`` advances as work progresses (through the Task).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    statement: str
    status: AcceptanceStatus = AcceptanceStatus.PENDING


class ContractBounds(BaseModel):
    """Stated task-level bounds the contract agreed (A4 authors; A3 enforces).

    All optional — A2 carries the *data*; A3 owns budgets-as-policy. The per-leg box
    (steps/budget/wall-clock) is separate (D-A2-2); these are whole-task limits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_budget_micros: int | None = None
    deadline: datetime | None = None
    max_legs: int | None = None

    @field_validator("deadline", mode="after")
    @classmethod
    def _deadline_tz_aware(cls, value: datetime | None) -> datetime | None:
        return _ensure_utc(value) if value is not None else None


class Contract(BaseModel):
    """The frozen, A4-authored anchor: goal, scope, acceptance criteria, bounds (D-A2-1).

    A leg re-reads this verbatim every reconstruction (the anti-drift discipline) and
    can never write it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str
    scope: str = ""
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    bounds: ContractBounds = ContractBounds()
    # The A3 per-task permission matrix (A4 authors; A3 enforces). Defaults to the
    # conservative seed (free categories allow, gated-by-default categories gate) so an
    # unconfigured task is bounded; rides ``contract_json``, no migration column.
    category_policy: CategoryPolicy = Field(default=DEFAULT_POLICY)
