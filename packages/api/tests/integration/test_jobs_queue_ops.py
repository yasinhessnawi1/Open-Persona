"""Integration tests for the durable job queue ops (Spec A0, T3).

Against real Postgres (built by ``alembic upgrade head`` via ``migrated_engine``,
the superuser engine — claim/heartbeat/reclaim are cross-tenant dispatch ops).
Proves the contracts the T3 review cares about: claim-then-commit (short txn, no
lock held while the handler would run), concurrent claimers never double-claim
under SKIP LOCKED, duplicate enqueue is an ON CONFLICT DO NOTHING no-op, and an
expired lease is reclaimed back to ``queued``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from persona.jobs import JobState
from persona_api.jobs import JobQueue
from sqlalchemy import text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


@pytest.fixture
def queue(migrated_engine: Engine) -> JobQueue:
    # Seed the owner once; the migrated schema is rebuilt per test.
    with migrated_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))
    return JobQueue(migrated_engine)


def _enqueue(queue: JobQueue, key: str, *, priority: int = 0, **kw: object) -> None:
    queue.enqueue(
        type="avatar_generation",
        owner_id="user_a",
        payload={"persona_id": key},
        idempotency_key=key,
        priority=priority,
        **kw,  # type: ignore[arg-type]
    )


# --- enqueue -----------------------------------------------------------------


def test_enqueue_returns_record_and_persists(queue: JobQueue, migrated_engine: Engine) -> None:
    rec = queue.enqueue(
        type="avatar_generation",
        owner_id="user_a",
        payload={"persona_id": "p1"},
        idempotency_key="avatar:p1:create",
    )
    assert rec is not None
    assert rec.state is JobState.QUEUED
    assert rec.attempt == 0
    assert rec.payload == {"persona_id": "p1"}
    with migrated_engine.begin() as conn:
        count = conn.execute(text("SELECT count(*) FROM jobs")).scalar()
    assert count == 1


def test_duplicate_enqueue_is_noop(queue: JobQueue, migrated_engine: Engine) -> None:
    first = queue.enqueue(
        type="avatar_generation",
        owner_id="user_a",
        payload={"persona_id": "p1"},
        idempotency_key="avatar:p1:create",
    )
    second = queue.enqueue(
        type="avatar_generation",
        owner_id="user_a",
        payload={"persona_id": "p1-different"},
        idempotency_key="avatar:p1:create",  # same key
    )
    assert first is not None
    assert second is None, "duplicate enqueue must be an ON CONFLICT DO NOTHING no-op"
    with migrated_engine.begin() as conn:
        count = conn.execute(text("SELECT count(*) FROM jobs")).scalar()
    assert count == 1


def test_same_idempotency_key_different_owners_both_enqueue(
    queue: JobQueue, migrated_engine: Engine
) -> None:
    # Owner-scoped uniqueness (security review T4): the SAME key for two different
    # owners must both enqueue — one tenant cannot suppress another's job by
    # pre-registering its key.
    with migrated_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_b','b@example.com')"))
    a = queue.enqueue(
        type="avatar_generation",
        owner_id="user_a",
        payload={},
        idempotency_key="avatar:shared:create",
    )
    b = queue.enqueue(
        type="avatar_generation",
        owner_id="user_b",
        payload={},
        idempotency_key="avatar:shared:create",  # same key, different owner
    )
    assert a is not None
    assert b is not None, "same key for a different owner must NOT be suppressed"
    with migrated_engine.begin() as conn:
        count = conn.execute(text("SELECT count(*) FROM jobs")).scalar()
    assert count == 2


# --- claim -------------------------------------------------------------------


def test_claim_marks_claimed_with_lease_and_commits(
    queue: JobQueue, migrated_engine: Engine
) -> None:
    _enqueue(queue, "k1")
    now = datetime.now(UTC)
    claimed = queue.claim(worker_id="w1", lease_seconds=30, now=now)
    assert len(claimed) == 1
    job = claimed[0]
    assert job.state is JobState.CLAIMED
    assert job.locked_by == "w1"
    assert job.attempt == 1  # incremented on claim
    assert job.lease_expires_at == now + timedelta(seconds=30)
    # Claim-then-commit: a SEPARATE connection sees the committed 'claimed' state
    # (the claim transaction is closed; no lock is held while work would run).
    with migrated_engine.begin() as conn:
        state = conn.execute(text("SELECT state FROM jobs WHERE id = :i"), {"i": job.id}).scalar()
    assert state == "claimed"


def test_claim_skips_future_scheduled_jobs(queue: JobQueue) -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    _enqueue(queue, "future", scheduled_at=future)
    assert queue.claim(worker_id="w1", lease_seconds=30) == []


def test_claim_orders_by_priority_then_schedule(queue: JobQueue) -> None:
    _enqueue(queue, "low", priority=0)
    _enqueue(queue, "high", priority=10)
    claimed = queue.claim(worker_id="w1", lease_seconds=30, limit=1)
    assert claimed[0].idempotency_key == "high"


def test_concurrent_claimers_never_double_claim(queue: JobQueue) -> None:
    # 50 queued jobs, two workers claiming concurrently must partition them with
    # zero overlap and zero loss — the SKIP LOCKED guarantee.
    for i in range(50):
        _enqueue(queue, f"job-{i:02d}")

    def claim_all(worker_id: str) -> list[str]:
        ids: list[str] = []
        while batch := queue.claim(worker_id=worker_id, lease_seconds=30, limit=5):
            ids.extend(j.id for j in batch)
        return ids

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(claim_all, "w1")
        b = pool.submit(claim_all, "w2")
        ids_a, ids_b = a.result(), b.result()

    assert set(ids_a).isdisjoint(ids_b), "SKIP LOCKED double-claim: overlap between workers"
    assert len(ids_a) + len(ids_b) == 50, "every job claimed exactly once"


# --- heartbeat / lifecycle ---------------------------------------------------


def test_heartbeat_extends_lease_only_for_owning_worker(queue: JobQueue) -> None:
    _enqueue(queue, "k1")
    queue.claim(worker_id="w1", lease_seconds=30)
    later = datetime.now(UTC) + timedelta(seconds=60)
    assert queue.heartbeat(job_id=_only_id(queue), worker_id="w1", lease_seconds=30, now=later)
    assert not queue.heartbeat(
        job_id=_only_id(queue), worker_id="w2", lease_seconds=30, now=later
    ), "a non-owning worker must not be able to renew the lease"


def test_mark_running_then_complete(queue: JobQueue, migrated_engine: Engine) -> None:
    _enqueue(queue, "k1")
    queue.claim(worker_id="w1", lease_seconds=30)
    jid = _only_id(queue)
    assert queue.mark_running(job_id=jid, worker_id="w1")
    assert queue.complete(job_id=jid, worker_id="w1")
    with migrated_engine.begin() as conn:
        row = conn.execute(
            text("SELECT state, locked_by, lease_expires_at FROM jobs WHERE id = :i"), {"i": jid}
        ).one()
    assert row.state == "succeeded"
    assert row.locked_by is None
    assert row.lease_expires_at is None


def test_complete_rejects_non_owner(queue: JobQueue) -> None:
    _enqueue(queue, "k1")
    queue.claim(worker_id="w1", lease_seconds=30)
    jid = _only_id(queue)
    queue.mark_running(job_id=jid, worker_id="w1")
    assert not queue.complete(job_id=jid, worker_id="w2")


# --- reclaim_expired ---------------------------------------------------------


def test_reclaim_expired_returns_job_to_queued(queue: JobQueue, migrated_engine: Engine) -> None:
    _enqueue(queue, "k1")
    queue.claim(worker_id="w1", lease_seconds=30)
    jid = _only_id(queue)
    # Force the lease into the past (simulate a dead worker that stopped heartbeating).
    with migrated_engine.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET lease_expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": jid},
        )
    reclaimed = queue.reclaim_expired()
    assert reclaimed == 1
    with migrated_engine.begin() as conn:
        row = conn.execute(
            text("SELECT state, locked_by, lease_expires_at FROM jobs WHERE id = :i"), {"i": jid}
        ).one()
    assert row.state == "queued"
    assert row.locked_by is None
    assert row.lease_expires_at is None
    # And it is claimable again.
    assert len(queue.claim(worker_id="w2", lease_seconds=30)) == 1


def test_reclaim_ignores_live_lease(queue: JobQueue) -> None:
    _enqueue(queue, "k1")
    queue.claim(worker_id="w1", lease_seconds=300)  # long, live lease
    assert queue.reclaim_expired() == 0


def _only_id(queue: JobQueue) -> str:
    # Test helper: the single job's id, read via a fresh claim-free query.
    with queue._engine.begin() as conn:  # noqa: SLF001 — test introspection
        return conn.execute(text("SELECT id FROM jobs LIMIT 1")).scalar_one()
