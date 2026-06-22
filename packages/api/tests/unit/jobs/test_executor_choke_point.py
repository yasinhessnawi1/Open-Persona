"""Unit tests for the executor's RLS-GUC choke point (Spec A0, T4).

No DB: a fake queue + a probe handler that records the ``current_user_id``
contextvar it observed. Proves the choke point binds the job owner's scope for
the handler run and RELEASES it unconditionally — including when the handler
raises (the ``finally``). The cross-tenant adversarial proof (a handler actually
reading another tenant's rows → zero) is the integration test.
"""

# ruff: noqa: ARG002, ANN401 — protocol-required handler args (unused / Any context).
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from persona.jobs import JobPayload, JobRegistry, JobState, JobTypeSpec
from persona_api.jobs.executor import JobExecutor
from persona_api.jobs.queue import JobRecord
from persona_api.middleware.rls_context import current_user_id


class _Payload(JobPayload):
    marker: str = "m"


class _ProbeHandler:
    """Records the owner scope it saw at handle time; optionally raises."""

    def __init__(self, *, boom: bool = False) -> None:
        self.seen_owner: str | None = "UNSET"
        self._boom = boom

    async def handle(self, payload: _Payload, context: Any) -> None:
        self.seen_owner = current_user_id.get()
        if self._boom:
            msg = "handler failure"
            raise RuntimeError(msg)


class _FakeQueue:
    """Records lifecycle calls; mark_running/complete succeed by default."""

    def __init__(self, *, running: bool = True, completed: bool = True) -> None:
        self.running = running
        self.completed = completed
        self.completed_called = False

        self.retried_called = False

    def mark_running(self, *, job_id: str, worker_id: str) -> bool:  # noqa: ARG002
        return self.running

    def complete(self, *, job_id: str, worker_id: str) -> bool:  # noqa: ARG002
        self.completed_called = True
        return self.completed

    def retry(self, *, job_id: str, worker_id: str, error: str, scheduled_at: object) -> bool:  # noqa: ARG002
        self.retried_called = True
        return True

    def mark_dead(self, *, job_id: str, worker_id: str, error: str) -> bool:  # noqa: ARG002
        return True

    def mark_failed(self, *, job_id: str, worker_id: str, error: str) -> bool:  # noqa: ARG002
        return True


def _record(owner_id: str = "user_a") -> JobRecord:
    now = datetime.now(UTC)
    return JobRecord(
        id="job-1",
        type="probe",
        owner_id=owner_id,
        payload={"marker": "m"},
        idempotency_key="probe:1",
        state=JobState.CLAIMED,
        priority=0,
        attempt=1,
        max_attempts=5,
        scheduled_at=now,
        created_at=now,
        lease_expires_at=None,
        locked_by="w1",
        last_error=None,
    )


def _executor(queue: _FakeQueue, handler: _ProbeHandler) -> JobExecutor:
    registry = JobRegistry(
        [
            JobTypeSpec(
                type="probe",
                payload_model=_Payload,
                handler=handler,
                idempotency_key=lambda p: f"probe:{p.marker}",
            )
        ]
    )
    return JobExecutor(
        queue=queue,  # type: ignore[arg-type]
        registry=registry,
        rls_engine=MagicMock(),  # unused: the probe handler does not open a connection
        worker_id="w1",
    )


@pytest.mark.asyncio
async def test_choke_point_binds_owner_scope_during_handler() -> None:
    handler = _ProbeHandler()
    outcome = await _executor(_FakeQueue(), handler).execute(_record("user_a"))
    assert handler.seen_owner == "user_a", "handler must run under the job owner's GUC scope"
    assert outcome is JobState.SUCCEEDED


@pytest.mark.asyncio
async def test_choke_point_releases_scope_after_success() -> None:
    assert current_user_id.get() is None
    await _executor(_FakeQueue(), _ProbeHandler()).execute(_record("user_a"))
    assert current_user_id.get() is None, "the contextvar must be reset after execution"


@pytest.mark.asyncio
async def test_choke_point_releases_scope_after_handler_raises() -> None:
    assert current_user_id.get() is None
    handler = _ProbeHandler(boom=True)
    queue = _FakeQueue()
    # A transient RuntimeError schedules a retry (T6) — the point here is that the
    # GUC is released through the failure path, whatever the outcome.
    outcome = await _executor(queue, handler).execute(_record("user_a"))
    assert outcome is JobState.QUEUED
    assert queue.retried_called
    assert handler.seen_owner == "user_a"  # it WAS bound before the raise
    assert current_user_id.get() is None, "the finally must reset the GUC even on failure"


@pytest.mark.asyncio
async def test_handler_failure_does_not_complete_the_job() -> None:
    queue = _FakeQueue()
    await _executor(queue, _ProbeHandler(boom=True)).execute(_record())
    assert not queue.completed_called, "a raised handler must not mark the job complete"


@pytest.mark.asyncio
async def test_lost_lease_before_run_short_circuits() -> None:
    handler = _ProbeHandler()
    queue = _FakeQueue(running=False)  # mark_running fails → lease lost
    outcome = await _executor(queue, handler).execute(_record())
    assert outcome is JobState.FAILED
    assert handler.seen_owner == "UNSET", "the handler must not run if the lease was lost"
