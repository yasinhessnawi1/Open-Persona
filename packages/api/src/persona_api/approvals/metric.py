"""The asks-per-task gate-fatigue metric — the A3-R-1 calibration instrument (A3, T14).

Gate fatigue is the first-order safety parameter: a system that asks too often trains the user to
rubber-stamp, and a rubber-stamping user has no gates. So the number of *runtime* approval asks per
task is instrumented from day one — what gets measured stays honest.

The metric is a pure **projection over the durable proposal rows**: every recorded
:class:`~persona.approvals.ActionProposal` is exactly one ask, so asks-per-task is just the count of
``approval_proposals`` for a task. This is:

- **attributable** — per task, and per persona (the proposal row carries both);
- **double-count-proof** — the one-pending-per-task idempotency (T6) means a re-delivered gated leg
  *reuses* the existing pending row rather than recording a second, so a redelivery never inflates
  the count;
- **inert** — a read-only ``SELECT COUNT`` that never touches the at-most-once status CAS, so
  observing the approval flow can never perturb it.

The A3-R-1 target — realistic tasks average *few* (ideally zero) runtime asks — is read off
:meth:`mean_asks_per_task` (asks summed over a persona's tasks ÷ its task count, so a zero-ask task
correctly pulls the mean down).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from persona_api.db.engine import rls_connection
from persona_api.db.models import approval_proposals as proposals_t
from persona_api.db.models import tasks as tasks_t

if TYPE_CHECKING:
    from sqlalchemy import Engine

__all__ = ["GateFatigueMetric"]


class GateFatigueMetric:
    """Reads asks-per-task from the durable proposal rows (RLS-scoped, inert)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def asks_for_task(self, owner_id: str, task_id: str) -> int:
        """The number of runtime approval asks recorded for one task (its proposal count)."""
        with rls_connection(self._engine, owner_id) as conn:
            return conn.execute(
                select(func.count())
                .select_from(proposals_t)
                .where(proposals_t.c.task_id == task_id)
            ).scalar_one()

    def total_asks(self, owner_id: str, persona_id: str) -> int:
        """The total runtime asks across all of a persona's tasks."""
        with rls_connection(self._engine, owner_id) as conn:
            return conn.execute(
                select(func.count())
                .select_from(proposals_t)
                .where(proposals_t.c.persona_id == persona_id)
            ).scalar_one()

    def task_count(self, owner_id: str, persona_id: str) -> int:
        """The persona's task count (the mean's denominator — includes zero-ask tasks)."""
        with rls_connection(self._engine, owner_id) as conn:
            return conn.execute(
                select(func.count()).select_from(tasks_t).where(tasks_t.c.persona_id == persona_id)
            ).scalar_one()

    def mean_asks_per_task(self, owner_id: str, persona_id: str) -> float:
        """The A3-R-1 number: asks ÷ tasks (a zero-ask task pulls it down). ``0.0`` if no tasks."""
        tasks = self.task_count(owner_id, persona_id)
        if tasks == 0:
            return 0.0
        return self.total_asks(owner_id, persona_id) / tasks
