"""Per-job metering tests (Spec A0, T8) — criterion 9.

Against real Postgres. Proves a job's spend is recorded into the EXISTING
observability ledger (``audit_log``, no new/forked table) and is **attributable
per job** (``target = job_id``, ``user_id = owner``). A0 *meters* (records); the
credit-deduct path is a charged handler's concern (proven later) — an
avatar-style handler records spend at ``credits_charged = 0`` to prove attribution
without deducting.
"""

# ruff: noqa: ARG001, ARG002 — fixture-ordering param + protocol-required handler args.
from __future__ import annotations

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
        pytest.skip("APP_DATABASE_URL not set; skipping metering test")
    return create_engine(app_url.replace("+asyncpg", "+psycopg"))


@pytest.fixture
def seeded(migrated_engine: Engine) -> Engine:
    with migrated_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))
    return migrated_engine


class _Payload(JobPayload):
    pass


class _MeteringHandler:
    """An avatar-style handler: records model + external spend, charges 0 credits."""

    async def handle(self, payload: _Payload, context: JobContext) -> None:
        context.meter(amount_micros=50_000, kind="model", detail={"provider": "openai"})
        context.meter(amount_micros=12_000, kind="external", detail={"service": "fal"})


def _spend_rows(engine: Engine, job_id: str) -> list[dict[str, str]]:
    with engine.begin() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT user_id, action, target, metadata FROM audit_log "
                    "WHERE action = 'job.spend' AND target = :j ORDER BY metadata->>'kind'"
                ),
                {"j": job_id},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def test_job_spend_recorded_and_attributable_per_job(seeded: Engine, app_engine: Engine) -> None:
    registry = JobRegistry(
        [
            JobTypeSpec(
                type="avatar_generation",
                payload_model=_Payload,
                handler=_MeteringHandler(),
                idempotency_key=lambda _p: "k",
            )
        ]
    )
    worker = Worker(
        dispatch_engine=seeded, rls_engine=app_engine, registry=registry, worker_id="w1"
    )
    job = JobQueue(seeded).enqueue(
        type="avatar_generation", owner_id="user_a", payload={}, idempotency_key="avatar:p1:create"
    )
    assert job is not None

    import asyncio

    assert asyncio.run(worker.run_once()) == 1

    rows = _spend_rows(seeded, job.id)
    # Two spend events, BOTH attributable to this job + owner, in the existing ledger.
    assert len(rows) == 2
    assert {r["target"] for r in rows} == {job.id}
    assert {r["user_id"] for r in rows} == {"user_a"}
    by_kind = {r["metadata"]["kind"]: r["metadata"] for r in rows}
    assert by_kind["model"]["amount_micros"] == "50000"
    assert by_kind["model"]["provider"] == "openai"
    assert by_kind["model"]["job_type"] == "avatar_generation"
    assert by_kind["external"]["amount_micros"] == "12000"
    # Total spend is summable per job (accumulation — A2 accounts on this).
    total = sum(int(r["metadata"]["amount_micros"]) for r in rows)
    assert total == 62_000


def test_metering_does_not_charge_credits(seeded: Engine, app_engine: Engine) -> None:
    # Avatar-style: spend is RECORDED (attribution) but credits are untouched —
    # the deduct path is a charged handler's concern (D-A0-X-metering-bar).
    with seeded.begin() as conn:
        conn.execute(text("INSERT INTO credits (user_id, balance) VALUES ('user_a', 100)"))
    registry = JobRegistry(
        [
            JobTypeSpec(
                type="avatar_generation",
                payload_model=_Payload,
                handler=_MeteringHandler(),
                idempotency_key=lambda _p: "k",
            )
        ]
    )
    worker = Worker(
        dispatch_engine=seeded, rls_engine=app_engine, registry=registry, worker_id="w1"
    )
    job = JobQueue(seeded).enqueue(
        type="avatar_generation", owner_id="user_a", payload={}, idempotency_key="avatar:p1:create"
    )
    assert job is not None

    import asyncio

    outcome = asyncio.run(worker.run_once())
    assert outcome == 1

    with seeded.begin() as conn:
        balance = conn.execute(
            text("SELECT balance FROM credits WHERE user_id = 'user_a'")
        ).scalar_one()
    assert balance == 100, "metering records spend; it must NOT deduct credits (avatar=0)"
    # ...yet the spend IS recorded + attributable, and the job succeeded.
    assert len(_spend_rows(seeded, job.id)) == 2
    with seeded.begin() as conn:
        state = conn.execute(
            text("SELECT state FROM jobs WHERE id = :i"), {"i": job.id}
        ).scalar_one()
    assert state == "succeeded"
