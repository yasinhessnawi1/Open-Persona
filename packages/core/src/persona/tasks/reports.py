"""Task outcome reports — completion, stuck, cancellation (Spec A2, T9).

Three **distinct** frozen projections over the durable task state, so a failure can never be
rendered as a success (the honesty discipline is enforced at the type level):

- :class:`CompletionReport` — the durable record of a finished task (done / artifacts / cost),
  read straight from the **durable ledger** + the latest checkpoint + the contract. The
  substance of the C0 "I've finished" message A3/A4 voice (criterion 9).
- :class:`StuckReport` — produced when a task fails after A0's leg retries exhaust: the honest
  *cause / where-it-stood / what's-next*, never a disguised completion. A3 speaks it; the task
  sits ``waiting(on_user)`` so the user can intervene (no silent terminal).
- :class:`CancellationSummary` — an honest where-things-stood for a user-cancelled task.

These are **pure projections** (no new storage): the inputs — ``task.ledger`` (durable),
``task.contract``, the latest checkpoint — are already durable; A6 builds the view on demand.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, field_validator

from persona.tasks.checkpoint import ArtifactPointer  # noqa: TC001 — Pydantic field type
from persona.tasks.contract import AcceptanceCriterion  # noqa: TC001 — Pydantic field type

if TYPE_CHECKING:
    from persona.tasks.checkpoint import TaskCheckpoint
    from persona.tasks.entity import Task

__all__ = [
    "CancellationSummary",
    "CompletionReport",
    "StuckReport",
    "build_cancellation_summary",
    "build_completion_report",
    "build_stuck_report",
]


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        msg = "naive datetime not allowed; use datetime.now(UTC) or attach a tzinfo"
        raise ValueError(msg)
    return value.astimezone(UTC)


class CompletionReport(BaseModel):
    """The durable record of a completed task (done / artifacts / cost). Read from the ledger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    goal: str
    conclusions: tuple[str, ...]
    artifacts: tuple[ArtifactPointer, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    model_micros: int
    sandbox_micros: int
    external_micros: int
    total_micros: int
    completed_at: datetime

    @field_validator("completed_at", mode="after")
    @classmethod
    def _tz(cls, value: datetime) -> datetime:
        return _ensure_utc(value)


class StuckReport(BaseModel):
    """An honest failure record: cause / where-it-stood / what's-next. NEVER a completion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    cause: str
    where_it_stood: tuple[str, ...]
    open_questions: tuple[str, ...]
    next_step: str
    total_micros: int
    stuck_at: datetime

    @field_validator("stuck_at", mode="after")
    @classmethod
    def _tz(cls, value: datetime) -> datetime:
        return _ensure_utc(value)


class CancellationSummary(BaseModel):
    """An honest where-things-stood for a user-cancelled task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    where_it_stood: tuple[str, ...]
    next_step: str
    total_micros: int
    cancelled_at: datetime

    @field_validator("cancelled_at", mode="after")
    @classmethod
    def _tz(cls, value: datetime) -> datetime:
        return _ensure_utc(value)


def build_completion_report(
    task: Task, checkpoint: TaskCheckpoint | None, *, now: datetime
) -> CompletionReport:
    """Project a :class:`CompletionReport` from the durable task + latest checkpoint.

    Cost comes from the durable ``task.ledger`` (not a re-derivation); conclusions + artifacts
    from the latest checkpoint; the goal + criteria from the contract.
    """
    return CompletionReport(
        task_id=task.id,
        goal=task.contract.goal,
        conclusions=checkpoint.progress_conclusions if checkpoint is not None else (),
        artifacts=checkpoint.artifact_pointers if checkpoint is not None else (),
        acceptance_criteria=task.contract.acceptance_criteria,
        model_micros=task.ledger.model_micros,
        sandbox_micros=task.ledger.sandbox_micros,
        external_micros=task.ledger.external_micros,
        total_micros=task.ledger.total_micros,
        completed_at=now,
    )


def build_stuck_report(
    task: Task, checkpoint: TaskCheckpoint | None, *, cause: str, now: datetime
) -> StuckReport:
    """Project an honest :class:`StuckReport` (the real ``cause`` + the actual progress)."""
    return StuckReport(
        task_id=task.id,
        cause=cause,
        where_it_stood=checkpoint.progress_conclusions if checkpoint is not None else (),
        open_questions=checkpoint.open_questions if checkpoint is not None else (),
        next_step=checkpoint.next_step if checkpoint is not None else "",
        total_micros=task.ledger.total_micros,
        stuck_at=now,
    )


def build_cancellation_summary(
    task: Task, checkpoint: TaskCheckpoint | None, *, now: datetime
) -> CancellationSummary:
    """Project an honest :class:`CancellationSummary` for a user-cancelled task."""
    return CancellationSummary(
        task_id=task.id,
        where_it_stood=checkpoint.progress_conclusions if checkpoint is not None else (),
        next_step=checkpoint.next_step if checkpoint is not None else "",
        total_micros=task.ledger.total_micros,
        cancelled_at=now,
    )
