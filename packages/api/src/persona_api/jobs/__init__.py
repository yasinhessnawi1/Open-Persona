"""persona_api.jobs — the durable Postgres job queue + worker service (Spec A0).

The api-side home of the durable execution substrate: the SKIP-LOCKED claim/
lease/heartbeat queue (``queue``) over the ``jobs`` table, and (later tasks) the
worker service that drives it. persona-core owns the job model + state machine +
typed handler registry; this layer is the persistence + dispatch.
"""

from __future__ import annotations

from persona_api.jobs.context import WorkerJobContext
from persona_api.jobs.executor import JobExecutor
from persona_api.jobs.queue import JobQueue, JobRecord
from persona_api.jobs.worker import Worker, build_worker, make_worker_id

__all__ = [
    "JobExecutor",
    "JobQueue",
    "JobRecord",
    "Worker",
    "WorkerJobContext",
    "build_worker",
    "make_worker_id",
]
