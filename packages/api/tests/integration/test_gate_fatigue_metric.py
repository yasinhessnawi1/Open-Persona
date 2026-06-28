"""The asks-per-task gate-fatigue metric (Spec A3, T14; criterion 10, A3-R-1).

Against real Postgres. The metric must be correct + attributable (counts real proposals per
task/persona, no double-count under a one-pending redelivery) and **inert** (a read-only
projection that never perturbs the at-most-once status CAS it measures).
"""

# ruff: noqa: ARG001 — ``migrated_engine`` is a dependency-ordering fixture param.
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from persona.approvals import ActionProposal, ProposalStatus
from persona.tasks import Contract, Task
from persona.tools import ActionCategory
from persona_api.approvals import ApprovalStore, GateFatigueMetric
from persona_api.tasks.store import TaskStore
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Iterator[Engine]:
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL (non-superuser role) not set; skipping metric test")
    engine = create_engine(app_url.replace("+asyncpg", "+psycopg"))
    yield engine
    engine.dispose()


def _seed(engine: Engine, user: str, persona: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:u, :e) ON CONFLICT (id) DO NOTHING"),
            {"u": user, "e": f"{user}@example.com"},
        )
        conn.execute(
            text("INSERT INTO personas (id, owner_id, yaml) VALUES (:p, :o, 'name: x')"),
            {"p": persona, "o": user},
        )


def _task(tasks: TaskStore, owner: str, persona: str, task_id: str) -> None:
    tasks.create(
        Task(
            id=task_id,
            owner_id=owner,
            persona_id=persona,
            contract=Contract(goal="g"),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )


def _proposal(owner: str, persona: str, task: str, pid: str) -> ActionProposal:
    return ActionProposal(
        proposal_id=pid,
        owner_id=owner,
        task_id=task,
        persona_id=persona,
        categories=frozenset({ActionCategory.SPEND}),
        tool_name="book",
        arguments={"amount": 100},
        description="book it",
        created_at=_NOW,
    )


def test_asks_for_task_counts_proposals(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    tasks = TaskStore(app_engine)
    _task(tasks, "user_a", "persona_a", "t1")
    approvals = ApprovalStore(app_engine)
    metric = GateFatigueMetric(app_engine)

    assert metric.asks_for_task("user_a", "t1") == 0
    approvals.create_proposal(_proposal("user_a", "persona_a", "t1", "p1"))
    # Resolve the first (out of pending) so a second sequential ask can be recorded.
    approvals.transition_proposal(
        "user_a", "p1", expected=ProposalStatus.PENDING, new=ProposalStatus.CONSUMED, now=_NOW
    )
    approvals.create_proposal(_proposal("user_a", "persona_a", "t1", "p2"))
    assert metric.asks_for_task("user_a", "t1") == 2  # two real sequential asks


def test_redelivery_does_not_double_count(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    _task(TaskStore(app_engine), "user_a", "persona_a", "t1")
    approvals = ApprovalStore(app_engine)
    metric = GateFatigueMetric(app_engine)
    approvals.create_proposal(_proposal("user_a", "persona_a", "t1", "p1"))
    # A re-delivered gated leg → one-pending returns the existing proposal, records no new row.
    approvals.create_proposal(_proposal("user_a", "persona_a", "t1", "p2"))
    assert metric.asks_for_task("user_a", "t1") == 1  # not 2 — the redelivery didn't inflate


def test_mean_asks_per_task_includes_zero_ask_tasks(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    tasks = TaskStore(app_engine)
    _task(tasks, "user_a", "persona_a", "t1")
    _task(tasks, "user_a", "persona_a", "t2")  # a task that never gates (the A3-R-1 ideal)
    approvals = ApprovalStore(app_engine)
    approvals.create_proposal(_proposal("user_a", "persona_a", "t1", "p1"))
    metric = GateFatigueMetric(app_engine)
    # 1 ask across 2 tasks → mean 0.5 (the zero-ask task pulls the average down, as A3-R-1 wants).
    assert metric.total_asks("user_a", "persona_a") == 1
    assert metric.task_count("user_a", "persona_a") == 2
    assert metric.mean_asks_per_task("user_a", "persona_a") == 0.5


def test_metric_is_inert_does_not_perturb_proposal(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    _task(TaskStore(app_engine), "user_a", "persona_a", "t1")
    approvals = ApprovalStore(app_engine)
    approvals.create_proposal(_proposal("user_a", "persona_a", "t1", "p1"))
    metric = GateFatigueMetric(app_engine)
    metric.asks_for_task("user_a", "t1")
    metric.mean_asks_per_task("user_a", "persona_a")
    # Reading the metric never touched the status CAS — the proposal is still pending.
    assert approvals.get_proposal("user_a", "p1").status is ProposalStatus.PENDING


def test_metric_is_attributable_per_persona(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    _seed(migrated_engine, "user_a", "persona_b")
    tasks = TaskStore(app_engine)
    _task(tasks, "user_a", "persona_a", "ta")
    _task(tasks, "user_a", "persona_b", "tb")
    approvals = ApprovalStore(app_engine)
    approvals.create_proposal(_proposal("user_a", "persona_a", "ta", "pa"))
    metric = GateFatigueMetric(app_engine)
    assert metric.total_asks("user_a", "persona_a") == 1
    assert metric.total_asks("user_a", "persona_b") == 0  # independent per persona
