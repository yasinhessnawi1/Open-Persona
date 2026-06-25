"""Task continuation — disposition→state, waiting/resume, dormancy (Spec A2, T8).

Runs against real Postgres. Proves the four holds: the disposition→state-machine wiring
(continue / complete / fail), the ``waiting(until_time)`` self-continuation (idempotent,
rides A0 ``scheduled_at``), zero-cost dormancy (a waiting task = a state row, no running leg),
and the ``waiting(on_user)`` wait + the ``UserReply`` resume seam (``EventTrigger`` reserved).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from persona.errors import TaskLegFailedError
from persona.tasks import Contract, Task, TaskState, UserReply, WaitKind
from persona_api.jobs.queue import JobQueue
from persona_api.tasks import TaskContinuation, TaskStore
from persona_runtime.agentic.run import Run, RunStatus
from persona_runtime.legs import LegDisposition, LegOutcome
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from persona.tasks import TaskCheckpoint  # isort: skip — grouped with task entities

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 25, 9, 0, tzinfo=UTC)
_LATER = _NOW + timedelta(hours=4)


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Iterator[Engine]:  # noqa: ARG001
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL (non-superuser role) not set; skipping RLS test")
    engine = create_engine(app_url.replace("+asyncpg", "+psycopg"))
    yield engine
    engine.dispose()


def _seed_active_task(engine: Engine) -> Task:
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))
        conn.execute(
            text(
                "INSERT INTO personas (id, owner_id, yaml) VALUES ('persona_a','user_a','name: x')"
            )
        )
    tasks = TaskStore(engine)
    tasks.create(
        Task(
            id="t1",
            owner_id="user_a",
            persona_id="persona_a",
            contract=Contract(goal="find the cheapest fare"),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    return tasks.start("user_a", "t1", now=_NOW)


def _outcome(
    task: Task, disposition: LegDisposition, *, resume_at: datetime | None = None
) -> LegOutcome:
    """A LegOutcome carrying a task with head=0 (post-append) — the continuation only reads
    task.id / task.head_checkpoint_seq / disposition / resume_at."""
    advanced = task.advance_checkpoint(0, now=_NOW)
    return LegOutcome(
        task=advanced,
        checkpoint=TaskCheckpoint(
            task_id="t1", leg_id="t1:leg:0", checkpoint_seq=0, next_step="x", updated_at=_NOW
        ),
        run=Run(
            persona_id="persona_a",
            task="x",
            status=RunStatus.COMPLETED,
            steps=[],
            started_at=_NOW,
            finished_at=_NOW,
        ),
        disposition=disposition,
        box_limit=None,
        spend={},
        resume_at=resume_at,
    )


def _leg_jobs(engine: Engine) -> list[dict[str, object]]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT state, scheduled_at, payload FROM jobs "
                "WHERE type = 'task_leg' AND owner_id = 'user_a'"
            )
        ).all()
    return [{"state": r.state, "scheduled_at": r.scheduled_at, "payload": r.payload} for r in rows]


# --- disposition → state machine ---------------------------------------------


def test_continue_immediate_enqueues_next_leg(migrated_engine: Engine, app_engine: Engine) -> None:
    task = _seed_active_task(migrated_engine)
    cont = TaskContinuation(task_store=TaskStore(app_engine), queue=JobQueue(app_engine))
    cont.apply("user_a", _outcome(task, LegDisposition.CONTINUE), now=_NOW)
    jobs = _leg_jobs(migrated_engine)
    assert len(jobs) == 1
    assert jobs[0]["payload"]["predecessor_seq"] == 0  # the new head
    assert TaskStore(app_engine).get("user_a", "t1").state == TaskState.ACTIVE  # stays active


def test_continue_is_idempotent(migrated_engine: Engine, app_engine: Engine) -> None:
    task = _seed_active_task(migrated_engine)
    cont = TaskContinuation(task_store=TaskStore(app_engine), queue=JobQueue(app_engine))
    cont.apply("user_a", _outcome(task, LegDisposition.CONTINUE), now=_NOW)
    cont.apply("user_a", _outcome(task, LegDisposition.CONTINUE), now=_NOW)  # double-enqueue
    assert len(_leg_jobs(migrated_engine)) == 1  # A2-R-4 key dedups


def test_completed_completes_task_with_no_next_job(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    task = _seed_active_task(migrated_engine)
    cont = TaskContinuation(task_store=TaskStore(app_engine), queue=JobQueue(app_engine))
    cont.apply("user_a", _outcome(task, LegDisposition.COMPLETED), now=_NOW)
    assert TaskStore(app_engine).get("user_a", "t1").state == TaskState.COMPLETED
    assert len(_leg_jobs(migrated_engine)) == 0


def test_failed_raises_for_retry(migrated_engine: Engine, app_engine: Engine) -> None:
    task = _seed_active_task(migrated_engine)
    cont = TaskContinuation(task_store=TaskStore(app_engine), queue=JobQueue(app_engine))
    with pytest.raises(TaskLegFailedError):
        cont.apply("user_a", _outcome(task, LegDisposition.FAILED), now=_NOW)
    assert TaskStore(app_engine).get("user_a", "t1").state == TaskState.ACTIVE  # unchanged
    assert len(_leg_jobs(migrated_engine)) == 0  # no continuation


# --- waiting(until_time): self-continuation + dormancy -----------------------


def test_wait_until_time_schedules_a_dormant_continuation(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    task = _seed_active_task(migrated_engine)
    cont = TaskContinuation(task_store=TaskStore(app_engine), queue=JobQueue(app_engine))
    cont.apply("user_a", _outcome(task, LegDisposition.CONTINUE, resume_at=_LATER), now=_NOW)

    fetched = TaskStore(app_engine).get("user_a", "t1")
    assert fetched.state == TaskState.WAITING
    assert fetched.wait_kind == WaitKind.UNTIL_TIME
    jobs = _leg_jobs(migrated_engine)
    assert len(jobs) == 1
    # Dormant: the continuation is QUEUED (not claimed/running) for the future instant.
    assert jobs[0]["state"] == "queued"
    assert jobs[0]["scheduled_at"] == _LATER


# --- waiting(on_user): zero-cost dormancy + the resume seam ------------------


def test_wait_on_user_is_zero_cost(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_active_task(migrated_engine)
    cont = TaskContinuation(task_store=TaskStore(app_engine), queue=JobQueue(app_engine))
    cont.wait_on_user("user_a", "t1", now=_NOW)
    fetched = TaskStore(app_engine).get("user_a", "t1")
    assert fetched.state == TaskState.WAITING
    assert fetched.wait_kind == WaitKind.ON_USER
    # Zero-cost: a state row and NO job — the reply will resume it.
    assert len(_leg_jobs(migrated_engine)) == 0


def test_resume_enqueues_leg_carrying_the_reply(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed_active_task(migrated_engine)
    cont = TaskContinuation(task_store=TaskStore(app_engine), queue=JobQueue(app_engine))
    cont.wait_on_user("user_a", "t1", now=_NOW)
    cont.resume("user_a", "t1", UserReply(reply="yes, Tuesday works"), now=_NOW)
    jobs = _leg_jobs(migrated_engine)
    assert len(jobs) == 1
    trigger = jobs[0]["payload"]["trigger"]
    assert trigger["kind"] == "user_reply"
    assert trigger["reply"] == "yes, Tuesday works"  # the reply rides into the next leg
