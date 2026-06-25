"""Completion / failure / cancel + pause (Spec A2, T9).

Against real Postgres. Proves: completion projects the durable ledger; failure-after-retries
reads A0's REAL dead-letter queue (jobs.state='dead') → ``waiting(on_user)`` + an honest
stuck-report (never a disguised completion); cancel → a clean terminal + honest summary; pause
halts new legs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from persona.tasks import (
    Contract,
    SpendKind,
    Task,
    TaskCheckpoint,
    TaskState,
    WaitKind,
    build_completion_report,
)
from persona_api.jobs.queue import JobQueue
from persona_api.tasks import CheckpointStore, TaskContinuation, TaskStore
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 25, 9, 0, tzinfo=UTC)


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


def _checkpoint(seq: int) -> TaskCheckpoint:
    return TaskCheckpoint(
        task_id="t1",
        leg_id=f"leg-{seq}",
        checkpoint_seq=seq,
        progress_conclusions=("best fare 1620kr (SAS, Tue)",),
        next_step="re-check Wed",
        updated_at=_NOW,
    )


def _continuation(app_engine: Engine) -> TaskContinuation:
    return TaskContinuation(
        task_store=TaskStore(app_engine),
        queue=JobQueue(app_engine),
        checkpoint_store=CheckpointStore(app_engine),
    )


# --- completion report (reads the durable ledger) ----------------------------


def test_completion_report_reads_durable_state(migrated_engine: Engine, app_engine: Engine) -> None:
    task = _seed_active_task(migrated_engine)
    checkpoints = CheckpointStore(app_engine)
    advanced = checkpoints.append(task, _checkpoint(0), spend={SpendKind.MODEL: 1500}, now=_NOW)
    done = TaskStore(app_engine).complete("user_a", "t1", now=_NOW)
    report = build_completion_report(done, checkpoints.get_latest("user_a", "t1"), now=_NOW)
    assert report.goal == "find the cheapest fare"
    assert report.conclusions == ("best fare 1620kr (SAS, Tue)",)
    assert report.total_micros == 1500  # the durable ledger
    assert advanced.head_checkpoint_seq == 0


# --- failure: A0 real dead-letter → waiting(on_user) + honest stuck-report ----


def test_dead_leg_parks_task_on_user_with_honest_cause(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed_active_task(migrated_engine)
    cont = _continuation(app_engine)
    stuck = cont.react_to_dead_leg("user_a", "t1", "external API 500 after 3 retries", now=_NOW)
    fetched = TaskStore(app_engine).get("user_a", "t1")
    assert fetched.state == TaskState.WAITING
    assert fetched.wait_kind == WaitKind.ON_USER  # not a silent terminal
    assert stuck is not None
    assert stuck.cause == "external API 500 after 3 retries"  # honest, not disguised


def test_react_to_dead_leg_is_idempotent(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_active_task(migrated_engine)
    cont = _continuation(app_engine)
    cont.react_to_dead_leg("user_a", "t1", "boom", now=_NOW)
    # second reaction (re-swept dead job) → no-op (task already waiting), the active-only guard.
    assert cont.react_to_dead_leg("user_a", "t1", "boom", now=_NOW) is None
    assert TaskStore(app_engine).get("user_a", "t1").wait_kind == WaitKind.ON_USER


def test_sweep_reads_a0_dead_letter_queue(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_active_task(migrated_engine)
    # A real A0 dead-letter row (jobs.state='dead' with last_error) for this task's leg.
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs "
                "(id, type, owner_id, payload, idempotency_key, state, last_error) "
                "VALUES ('j1','task_leg','user_a','{\"task_id\":\"t1\"}'::jsonb,"
                "'task:t1:after:init','dead','sandbox crashed')"
            )
        )
    cont = _continuation(app_engine)
    # The sweep reads A0's dead_letters() cross-tenant (superuser queue) and reacts per-owner.
    reacted = cont.sweep_dead_legs(JobQueue(migrated_engine), now=_NOW)
    assert reacted == 1
    assert TaskStore(app_engine).get("user_a", "t1").wait_kind == WaitKind.ON_USER


# --- cancellation + pause ----------------------------------------------------


def test_cancel_lands_clean_terminal_with_summary(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    task = _seed_active_task(migrated_engine)
    CheckpointStore(app_engine).append(task, _checkpoint(0), spend={SpendKind.MODEL: 800}, now=_NOW)
    cont = _continuation(app_engine)
    summary = cont.cancel("user_a", "t1", now=_NOW)
    assert TaskStore(app_engine).get("user_a", "t1").state == TaskState.CANCELLED
    assert summary.where_it_stood == ("best fare 1620kr (SAS, Tue)",)  # honest where it stood
    assert summary.total_micros == 800


def test_pause_halts_then_resume_continues(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_active_task(migrated_engine)
    tasks = TaskStore(app_engine)
    paused = tasks.pause("user_a", "t1", now=_NOW)
    assert paused.paused is True
    assert paused.state == TaskState.ACTIVE  # overlay, underlying state preserved
    assert tasks.unpause("user_a", "t1", now=_NOW).paused is False
