"""TaskStore + CheckpointStore — RLS-scoped, audited CRUD + the CAS append (Spec A2, T5).

Runs against real Postgres (``alembic upgrade head`` → migration 016). The stores run
under the ``persona_app`` non-superuser RLS engine, so a cross-tenant reach hits zero rows.
Concerns:

1. **Round-trip fidelity** — a created task reads back as the same entity (contract, ledger,
   state, linkage).
2. **Lifecycle transitions** — start/begin_wait/resume/complete/cancel/pause persist + audit;
   an illegal transition raises ``TaskStateError`` (the entity guard) and writes nothing.
3. **The CAS append (A2-R-4 at the store layer)** — driven by ``Task.advance_checkpoint``:
   the happy path advances head + ledger + writes the checkpoint; a re-delivery is a clean
   no-op (head unchanged, one checkpoint, no double-counted spend); the budget gate rejects
   an oversized checkpoint before any write.
4. **RLS isolation** — cross-tenant get/transition raises NotFound (no oracle).
5. **Audit** — exactly one ``audit_log`` row per mutation.
"""

# ruff: noqa: ARG001 — ``migrated_engine`` is a dependency-ordering fixture param.
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from persona.errors import CheckpointTooLargeError, TaskNotFoundError, TaskStateError
from persona.tasks import (
    Contract,
    CostLedger,
    SpendKind,
    Task,
    TaskCheckpoint,
    TaskState,
    WaitKind,
)
from persona_api.tasks import CheckpointStore, TaskStore
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 25, 9, 0, tzinfo=UTC)


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Iterator[Engine]:
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL (non-superuser role) not set; skipping RLS test")
    engine = create_engine(app_url.replace("+asyncpg", "+psycopg"))
    yield engine
    engine.dispose()


def _seed(engine: Engine, user_id: str, persona_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:u, :e)"),
            {"u": user_id, "e": f"{user_id}@example.com"},
        )
        conn.execute(
            text("INSERT INTO personas (id, owner_id, yaml) VALUES (:p, :o, 'name: x')"),
            {"p": persona_id, "o": user_id},
        )


def _task(task_id: str = "t1", owner: str = "user_a", persona: str = "persona_a") -> Task:
    return Task(
        id=task_id,
        owner_id=owner,
        persona_id=persona,
        contract=Contract(goal="find the cheapest fare", scope="under 2000kr"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _checkpoint(
    seq: int, *, task_id: str = "t1", next_step: str = "re-check Wed"
) -> TaskCheckpoint:
    return TaskCheckpoint(
        task_id=task_id,
        leg_id=f"leg-{seq}",
        checkpoint_seq=seq,
        progress_conclusions=("best fare 1620kr",),
        next_step=next_step,
        updated_at=_NOW,
    )


def _audit_count(engine: Engine, owner: str, action: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT count(*) FROM audit_log WHERE user_id = :u AND action = :a"),
            {"u": owner, "a": action},
        ).scalar_one()


# --- round-trip fidelity -----------------------------------------------------


def test_create_and_get_round_trip(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    store = TaskStore(app_engine)
    created = store.create(_task())
    fetched = store.get("user_a", "t1")
    assert fetched == created
    assert fetched.contract.goal == "find the cheapest fare"
    assert fetched.contract.scope == "under 2000kr"
    assert fetched.state == TaskState.DEFINED
    assert fetched.ledger == CostLedger()
    assert fetched.head_checkpoint_seq is None


def test_get_missing_raises(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    store = TaskStore(app_engine)
    with pytest.raises(TaskNotFoundError):
        store.get("user_a", "nope")


def test_list_for_owner(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    store = TaskStore(app_engine)
    store.create(_task("t1"))
    store.create(_task("t2"))
    ids = {t.id for t in store.list_for_owner("user_a")}
    assert ids == {"t1", "t2"}


# --- lifecycle transitions ---------------------------------------------------


def test_transitions_persist_and_audit(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    store = TaskStore(app_engine)
    store.create(_task())
    started = store.start("user_a", "t1", now=_NOW)
    assert started.state == TaskState.ACTIVE
    waiting = store.begin_wait("user_a", "t1", WaitKind.UNTIL_TIME, now=_NOW)
    assert waiting.state == TaskState.WAITING
    assert waiting.wait_kind == WaitKind.UNTIL_TIME
    # persisted, not just returned
    assert store.get("user_a", "t1").wait_kind == WaitKind.UNTIL_TIME
    resumed = store.resume("user_a", "t1", now=_NOW)
    assert resumed.state == TaskState.ACTIVE
    assert resumed.wait_kind is None
    done = store.complete("user_a", "t1", now=_NOW)
    assert done.state == TaskState.COMPLETED
    assert _audit_count(migrated_engine, "user_a", "task.create") == 1
    assert _audit_count(migrated_engine, "user_a", "task.complete") == 1


def test_pause_unpause_overlay(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    store = TaskStore(app_engine)
    store.create(_task())
    store.start("user_a", "t1", now=_NOW)
    paused = store.pause("user_a", "t1", now=_NOW)
    assert paused.paused is True
    assert paused.state == TaskState.ACTIVE
    assert store.unpause("user_a", "t1", now=_NOW).paused is False


def test_illegal_transition_raises_and_writes_nothing(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    store = TaskStore(app_engine)
    store.create(_task())
    # resume from DEFINED is illegal (must start → active → waiting first).
    with pytest.raises(TaskStateError):
        store.resume("user_a", "t1", now=_NOW)
    assert store.get("user_a", "t1").state == TaskState.DEFINED  # unchanged
    assert _audit_count(migrated_engine, "user_a", "task.resume") == 0


# --- RLS isolation -----------------------------------------------------------


def test_cross_tenant_get_raises_notfound(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    _seed(migrated_engine, "user_b", "persona_b")
    store = TaskStore(app_engine)
    store.create(_task("t1", owner="user_a", persona="persona_a"))
    with pytest.raises(TaskNotFoundError):
        store.get("user_b", "t1")  # user_b's RLS scope sees no row → no oracle
    with pytest.raises(TaskNotFoundError):
        store.start("user_b", "t1", now=_NOW)


# --- the CAS append (A2-R-4 at the store layer) ------------------------------


def test_append_advances_head_ledger_and_writes_checkpoint(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    tasks = TaskStore(app_engine)
    checkpoints = CheckpointStore(app_engine)
    tasks.create(_task())
    task = tasks.start("user_a", "t1", now=_NOW)

    advanced = checkpoints.append(task, _checkpoint(0), spend={SpendKind.MODEL: 1200}, now=_NOW)
    assert advanced.head_checkpoint_seq == 0
    assert advanced.ledger.model_micros == 1200

    latest = checkpoints.get_latest("user_a", "t1")
    assert latest is not None
    assert latest.checkpoint_seq == 0
    assert latest.next_step == "re-check Wed"
    assert _audit_count(migrated_engine, "user_a", "checkpoint.append") == 1


def test_append_redelivery_is_idempotent(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    tasks = TaskStore(app_engine)
    checkpoints = CheckpointStore(app_engine)
    tasks.create(_task())
    task = tasks.start("user_a", "t1", now=_NOW)

    # First delivery of leg 0.
    checkpoints.append(task, _checkpoint(0), spend={SpendKind.MODEL: 1200}, now=_NOW)
    # Re-delivery of the SAME leg (same in-memory task, head still None): clean no-op —
    # the ON CONFLICT no-ops the checkpoint and the head CAS matches no row.
    redelivered = checkpoints.append(task, _checkpoint(0), spend={SpendKind.MODEL: 1200}, now=_NOW)
    assert redelivered.head_checkpoint_seq == 0
    assert redelivered.ledger.model_micros == 1200  # NOT double-counted

    refetched = tasks.get("user_a", "t1")
    assert refetched.head_checkpoint_seq == 0
    assert refetched.ledger.model_micros == 1200
    assert len(checkpoints.list_recent("user_a", "t1", limit=10)) == 1  # one checkpoint

    # A genuine successor advances.
    advanced = checkpoints.append(refetched, _checkpoint(1), spend={SpendKind.MODEL: 300}, now=_NOW)
    assert advanced.head_checkpoint_seq == 1
    assert advanced.ledger.model_micros == 1500


def test_append_enforces_budget(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    tasks = TaskStore(app_engine)
    checkpoints = CheckpointStore(app_engine, token_budget=50)
    tasks.create(_task())
    task = tasks.start("user_a", "t1", now=_NOW)
    fat = TaskCheckpoint(
        task_id="t1",
        leg_id="leg-0",
        checkpoint_seq=0,
        progress_conclusions=tuple("conclusion " * 50 for _ in range(10)),
        next_step="x",
        updated_at=_NOW,
    )
    with pytest.raises(CheckpointTooLargeError):
        checkpoints.append(task, fat, spend={}, now=_NOW)
    # nothing written
    assert checkpoints.get_latest("user_a", "t1") is None
    assert tasks.get("user_a", "t1").head_checkpoint_seq is None
