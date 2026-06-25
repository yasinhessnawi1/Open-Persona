"""Task-scoped workspace lifecycle (Spec A2, T10; D-A2-6).

The task workspace is a **Spec-12 sandbox session keyed by ``task_id``** — files persist across
legs for the task's lifetime (pointed to by checkpoint ``artifact_pointers``). At a terminal
state (completed / cancelled / failed-abandoned):

1. Artifacts **referenced by the completion report** are copied out via the existing
   ``copy_produced_file_to`` (they survive per the existing artifact rules — the seam the
   completion report pins).
2. The task session is **``destroy_session``-ed** so scratch space doesn't leak, after a
   config retention window (``PERSONA_TASK_WORKSPACE_RETENTION_DAYS``, default 7) — a delayed
   cleanup the worker schedules at the terminal transition (A0 ``scheduled_at``); this module
   owns the destroy. ``destroy_session`` is idempotent (Spec-12), so a re-run is safe.

No new retention mechanism — this reuses Spec-12's session teardown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.logging import get_logger

if TYPE_CHECKING:
    from persona.sandbox.protocol import CodeSandbox

__all__ = [
    "TASK_WORKSPACE_RETENTION_DAYS_DEFAULT",
    "cleanup_task_workspace",
    "task_workspace_session_id",
]

#: Default retention window before a terminal task's workspace is destroyed (D-A2-6).
#: Env-overridable via ``PERSONA_TASK_WORKSPACE_RETENTION_DAYS`` at the composition root.
TASK_WORKSPACE_RETENTION_DAYS_DEFAULT = 7

_log = get_logger("api.tasks.workspace")


def task_workspace_session_id(task_id: str) -> str:
    """The Spec-12 session id for a task's workspace — stable across all its legs."""
    return f"task-{task_id}"


async def cleanup_task_workspace(sandbox: CodeSandbox, task_id: str) -> None:
    """Destroy a terminal task's workspace session (idempotent, Spec-12).

    Called at the retention deadline after a terminal transition. Completion-report-referenced
    artifacts must already have been copied out (the existing artifact rules); this releases
    the scratch space so a long-lived task doesn't leak its workspace.
    """
    session_id = task_workspace_session_id(task_id)
    await sandbox.destroy_session(session_id)
    _log.info("task workspace destroyed", task_id=task_id, session_id=session_id)
