"""Claim-time fairness tests (Spec A0, T7) — criterion 6.

Against real Postgres. Proves the per-user + global in-flight caps gate the claim:
a user already at the cap has its queued jobs skipped (so others are not starved),
the cap is exact under one-at-a-time claims, ``0`` means unlimited, and — the
headline — under a mixed load one user's flood does not starve another.
"""

# ruff: noqa: ARG001, ARG002, SLF001 — fixtures + protocol args + private internals.
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest
from persona.jobs import JobPayload, JobRegistry, JobTypeSpec
from persona_api.jobs import JobQueue, Worker
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from persona.jobs import JobContext

pytestmark = pytest.mark.integration


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Engine:
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set; skipping fairness test")
    return create_engine(app_url.replace("+asyncpg", "+psycopg"))


@pytest.fixture
def two_users(migrated_engine: Engine) -> Engine:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email) VALUES "
                "('user_a','a@example.com'),('user_b','b@example.com')"
            )
        )
    return migrated_engine


def _enqueue_many(queue: JobQueue, owner: str, n: int) -> None:
    for i in range(n):
        queue.enqueue(
            type="t",
            owner_id=owner,
            payload={},
            idempotency_key=f"{owner}:{i}",
        )


def test_per_user_cap_skips_a_user_already_at_cap(two_users: Engine) -> None:
    queue = JobQueue(two_users)
    _enqueue_many(queue, "user_a", 5)
    _enqueue_many(queue, "user_b", 2)
    # Force user_a to 3 in-flight (already at the cap).
    with two_users.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs SET state='running', locked_by='wX', "
                "lease_expires_at=now()+interval '1 hour' "
                "WHERE owner_id='user_a' AND id IN (SELECT id FROM jobs WHERE owner_id='user_a' "
                "LIMIT 3)"
            )
        )
    claimed = queue.claim(worker_id="w1", lease_seconds=30, limit=10, max_per_user=3)
    owners = {c.owner_id for c in claimed}
    assert owners == {"user_b"}, f"a user at the cap must be skipped; claimed owners={owners}"


def test_per_user_cap_is_exact_under_single_claims(two_users: Engine) -> None:
    queue = JobQueue(two_users)
    _enqueue_many(queue, "user_a", 5)
    got = 0
    while queue.claim(worker_id="w1", lease_seconds=30, limit=1, max_per_user=3):
        got += 1
        if got > 10:  # safety
            break
    assert got == 3, "exactly per_user_cap jobs claimable while none complete"


def test_global_cap_is_exact_under_single_claims(two_users: Engine) -> None:
    queue = JobQueue(two_users)
    _enqueue_many(queue, "user_a", 3)
    _enqueue_many(queue, "user_b", 3)
    got = 0
    while queue.claim(worker_id="w1", lease_seconds=30, limit=1, max_global=2):
        got += 1
        if got > 10:
            break
    assert got == 2, "exactly max_global jobs claimable across all users"


def test_zero_cap_means_unlimited(two_users: Engine) -> None:
    queue = JobQueue(two_users)
    _enqueue_many(queue, "user_a", 5)
    claimed = queue.claim(worker_id="w1", lease_seconds=30, limit=10, max_per_user=0)
    assert len(claimed) == 5, "max_per_user=0 must be unlimited"


# --- the criterion-6 headline: anti-starvation under a flood --------------------


class _Payload(JobPayload):
    pass


class _Slow:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    async def handle(self, payload: _Payload, context: JobContext) -> None:
        await asyncio.sleep(self.seconds)


def _succeeded(engine: Engine, owner: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT count(*) FROM jobs WHERE owner_id = :o AND state = 'succeeded'"),
            {"o": owner},
        ).scalar_one()


def test_flooding_user_does_not_starve_another(two_users: Engine, app_engine: Engine) -> None:
    queue = JobQueue(two_users)
    _enqueue_many(queue, "user_a", 12)  # the flood (enqueued FIRST → earlier scheduled_at)
    _enqueue_many(queue, "user_b", 3)  # the victim

    registry = JobRegistry(
        [
            JobTypeSpec(
                type="t",
                payload_model=_Payload,
                handler=_Slow(0.15),
                idempotency_key=lambda _p: "k",  # unused per-call (keys set at enqueue)
            )
        ]
    )
    worker = Worker(
        dispatch_engine=two_users,
        rls_engine=app_engine,
        registry=registry,
        worker_id="w1",
        concurrency=6,
        poll_interval_seconds=0.02,
        poll_jitter_seconds=0.0,
        max_jobs_per_user=3,  # user_a capped at 3 in flight → user_b's slots free up
    )

    async def drive() -> int:
        run_task = asyncio.create_task(worker.run())
        # Wait until user_b's 3 jobs all finish.
        for _ in range(200):
            if _succeeded(two_users, "user_b") == 3:
                break
            await asyncio.sleep(0.02)
        a_done_when_b_finished = _succeeded(two_users, "user_a")
        worker.request_drain()
        await asyncio.wait_for(run_task, timeout=15)
        return a_done_when_b_finished

    a_done = asyncio.run(drive())
    assert _succeeded(two_users, "user_b") == 3, "user_b must not be starved by user_a's flood"
    # The proof of interleaving: user_b finished while user_a still had work pending
    # (its flood was capped at 3 in flight, leaving room for user_b).
    assert a_done < 12, f"user_b should finish BEFORE user_a drains; user_a done={a_done}/12"
