"""Kill switches — task-cancel / persona-suspend / global-pause (Spec A3, T11; criterion 6).

Three immediate, audited stops at three scopes, and **the reason-scoped runnable invariant**
that keeps them from interfering:

- **task-cancel** — A2's clean cancel (state → ``CANCELLED``, **terminal**). Immediate: the next
  leg is prevented (a terminal task is never runnable) and a running leg reaches its next
  checkpoint via the executor's ``external_cancel`` token (A2's box mechanism), never mid-step.
  A cancelled task cannot be revived — a budget extension's ``cas_unpause`` requires
  ``paused=true`` (cancel clears it) and a terminal task is non-runnable regardless.
- **persona-suspend** — owner-scoped + RLS (a user suspends their own persona): no new legs for
  any of that persona's tasks; running legs finish their box. **Resumable.**
- **global-pause** — the operational, ownerless big-red-button: stop claiming autonomy jobs
  platform-wide. **Resumable.** Operator-authorised (the API route gates it; the ``actor`` is
  recorded on the row + the ``audit_log``).

**The invariant (non-negotiable, A3-D-5 carry):** a task is runnable only when **no** pause
source holds it. The sources are independent storage at independent scopes — the task ``paused``
overlay (budget, T10), the ``suspended_personas`` row (persona), and the ``platform_controls``
flag (global) — so clearing one (budget's ``cas_unpause``) can **never** resume a task another
source still holds. :meth:`KillSwitchStore.is_runnable` is the single combined guard the leg
handler consults before running a leg.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona.tasks import is_terminal
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from persona_api.db.engine import rls_connection
from persona_api.db.models import platform_controls as controls_t
from persona_api.db.models import suspended_personas as suspended_t
from persona_api.services import audit_service

if TYPE_CHECKING:
    from datetime import datetime

    from persona.tasks import CancellationSummary, Task
    from sqlalchemy import Engine

    from persona_api.tasks.continuation import TaskContinuation

__all__ = ["GLOBAL_PAUSE_KEY", "KillSwitchCommand", "KillSwitchStore", "parse_kill_switch"]

_log = get_logger("api.approvals.kill_switch")

#: The single ``platform_controls`` row key for the platform-wide autonomy pause.
GLOBAL_PAUSE_KEY = "global_autonomy_paused"


class KillSwitchCommand(StrEnum):
    """A kill-switch intent parsed from conversation (the caller applies it with context)."""

    CANCEL_TASK = "cancel_task"
    SUSPEND_PERSONA = "suspend_persona"
    RESUME_PERSONA = "resume_persona"
    GLOBAL_PAUSE = "global_pause"
    GLOBAL_RESUME = "global_resume"


# Conversation parsing (NO/EN), intent-only — the persona/task is the conversation context.
_GLOBAL_PAUSE = re.compile(
    r"\b(pause|stop|halt|stopp|pause)\b.*\b(every|all|everything|alt|alle|autonomy|autonomi)\b",
    re.IGNORECASE,
)
_GLOBAL_RESUME = re.compile(
    r"\b(resume|restart|unpause|fortsett|gjenoppta|start)\b.*\b(every|all|everything|alt|alle|autonomy|autonomi)\b",
    re.IGNORECASE,
)
_SUSPEND = re.compile(r"\b(suspend|pause|stopp|suspender)\b", re.IGNORECASE)
_RESUME = re.compile(r"\b(resume|unsuspend|fortsett|gjenoppta)\b", re.IGNORECASE)
_CANCEL = re.compile(r"\b(cancel|abort|drop|avbryt|kanseller)\b", re.IGNORECASE)


def parse_kill_switch(reply: str) -> KillSwitchCommand | None:
    """Parse a kill-switch intent from a natural reply (NO/EN); ``None`` if none recognised.

    Ordered most-specific-first (global before per-persona) so "pause everything" doesn't read
    as a persona suspend. Intent-only — never guesses a target; the caller supplies the
    persona/task from the conversation context.
    """
    if _GLOBAL_RESUME.search(reply):
        return KillSwitchCommand.GLOBAL_RESUME
    if _GLOBAL_PAUSE.search(reply):
        return KillSwitchCommand.GLOBAL_PAUSE
    if _CANCEL.search(reply):
        return KillSwitchCommand.CANCEL_TASK
    if _RESUME.search(reply):
        return KillSwitchCommand.RESUME_PERSONA
    if _SUSPEND.search(reply):
        return KillSwitchCommand.SUSPEND_PERSONA
    return None


class KillSwitchStore:
    """The three kill switches + the reason-scoped runnable guard (T11)."""

    def __init__(self, engine: Engine, *, continuation: TaskContinuation) -> None:
        self._engine = engine
        self._continuation = continuation

    # --- task-cancel (terminal; A2's clean cancel) -------------------------

    def cancel_task(self, owner_id: str, task_id: str, *, now: datetime) -> CancellationSummary:
        """Cancel a task → ``CANCELLED`` (terminal) + an honest where-it-stood (A2's cancel)."""
        return self._continuation.cancel(owner_id, task_id, now=now)

    # --- persona-suspend (owner-scoped, RLS; resumable) --------------------

    def suspend_persona(self, owner_id: str, persona_id: str, *, now: datetime) -> None:
        """Suspend a persona's autonomy — no new legs for its tasks. Idempotent."""
        with rls_connection(self._engine, owner_id) as conn:
            conn.execute(
                pg_insert(suspended_t)
                .values(owner_id=owner_id, persona_id=persona_id, suspended_at=now)
                .on_conflict_do_nothing(constraint="pk_suspended_personas")
            )
        self._audit(owner_id, "autonomy.persona_suspend", persona_id)

    def resume_persona(self, owner_id: str, persona_id: str, *, now: datetime) -> None:  # noqa: ARG002
        """Resume a persona's autonomy — delete the suspension row. Idempotent."""
        with rls_connection(self._engine, owner_id) as conn:
            conn.execute(
                delete(suspended_t).where(
                    suspended_t.c.owner_id == owner_id,
                    suspended_t.c.persona_id == persona_id,
                )
            )
        self._audit(owner_id, "autonomy.persona_resume", persona_id)

    def is_persona_suspended(self, owner_id: str, persona_id: str) -> bool:
        """True iff this owner's persona is suspended (a presence read)."""
        with rls_connection(self._engine, owner_id) as conn:
            row = conn.execute(
                select(suspended_t.c.persona_id).where(
                    suspended_t.c.owner_id == owner_id,
                    suspended_t.c.persona_id == persona_id,
                )
            ).first()
        return row is not None

    # --- global-pause (operational, ownerless; resumable) -----------------

    def global_pause(self, *, actor: str, now: datetime) -> None:
        """Pause autonomy platform-wide (operator-authorised; the route gates ``actor``)."""
        self._set_global(enabled=True, actor=actor, now=now)
        self._audit(actor, "autonomy.global_pause", GLOBAL_PAUSE_KEY)

    def global_resume(self, *, actor: str, now: datetime) -> None:
        """Resume autonomy platform-wide (operator-authorised)."""
        self._set_global(enabled=False, actor=actor, now=now)
        self._audit(actor, "autonomy.global_resume", GLOBAL_PAUSE_KEY)

    def is_globally_paused(self) -> bool:
        """True iff platform-wide autonomy is paused (the worker reads this before claiming)."""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(controls_t.c.enabled).where(controls_t.c.key == GLOBAL_PAUSE_KEY)
            ).first()
        return bool(row.enabled) if row is not None else False

    # --- the reason-scoped runnable invariant ------------------------------

    def is_runnable(self, owner_id: str, task: Task) -> bool:
        """A task is runnable only when NO pause source holds it (the T11 invariant).

        Independent sources: terminal state, the budget ``paused`` overlay (T10),
        persona-suspend, global-pause. Clearing any one (e.g. a budget extension) leaves the
        others holding — so the task does not resume while another source still applies.
        """
        if is_terminal(task.state):
            return False
        if task.paused:  # budget (T10) — task-level overlay
            return False
        if self.is_globally_paused():
            return False
        return not self.is_persona_suspended(owner_id, task.persona_id)

    # --- internals ----------------------------------------------------------

    def _set_global(self, *, enabled: bool, actor: str, now: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                pg_insert(controls_t)
                .values(key=GLOBAL_PAUSE_KEY, enabled=enabled, actor=actor, updated_at=now)
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={"enabled": enabled, "actor": actor, "updated_at": now},
                )
            )

    def _audit(self, user_id: str, action: str, target: str) -> None:
        audit_service.record(
            engine=self._engine, user_id=user_id, action=action, target=target, metadata={}
        )
