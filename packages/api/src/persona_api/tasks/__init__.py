"""persona_api.tasks — the durable side of the autonomous task model (Spec A2).

The RLS-scoped, audited stores over the ``tasks`` + ``task_checkpoints`` tables:
:class:`TaskStore` (entity CRUD + lifecycle) and :class:`CheckpointStore` (the append-only
checkpoint sequence + the atomic CAS write — the durable half of A2-R-4). The pure entity,
state machine, and checkpoint contracts live in ``persona.tasks`` (core).
"""

from __future__ import annotations

from persona_api.tasks.continuation import TaskContinuation
from persona_api.tasks.handler import (
    TASK_LEG_JOB_TYPE,
    LegRunnerBuilder,
    TaskLegHandler,
    TaskLegPayload,
    enqueue_task_leg,
    register_task_leg_handler,
    task_leg_idempotency_key,
)
from persona_api.tasks.store import CheckpointStore, TaskStore
from persona_api.tasks.workspace import (
    TASK_WORKSPACE_RETENTION_DAYS_DEFAULT,
    cleanup_task_workspace,
    task_workspace_session_id,
)

__all__ = [
    "TASK_LEG_JOB_TYPE",
    "TASK_WORKSPACE_RETENTION_DAYS_DEFAULT",
    "CheckpointStore",
    "LegRunnerBuilder",
    "TaskContinuation",
    "TaskLegHandler",
    "TaskLegPayload",
    "TaskStore",
    "cleanup_task_workspace",
    "enqueue_task_leg",
    "register_task_leg_handler",
    "task_leg_idempotency_key",
    "task_workspace_session_id",
]
