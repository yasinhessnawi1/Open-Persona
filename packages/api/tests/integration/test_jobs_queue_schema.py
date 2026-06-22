"""Migration + RLS + claim-index integration tests for the job queue (Spec A0, T2).

Runs against a real Postgres built by ``alembic upgrade head`` (so migration 011
and its RLS policies are present). Two halves:

1. **RLS tenant isolation** on ``jobs`` and ``jobs_archive`` — adversarial, the
   gate before T9: insert rows for user_a AND user_b as superuser, then query
   under each user's RLS context as the NON-SUPERUSER ``persona_app`` role and
   assert zero cross-tenant rows + WITH CHECK blocks a cross-tenant insert +
   fail-closed when the GUC is unset.
2. **Claim-index proof** — EXPLAIN shows the partial ``idx_jobs_claim`` index
   serves the claim predicate, and the idempotency UNIQUE constraint dedups.

The non-superuser role is mandatory: superusers bypass RLS even under FORCE.
``APP_DATABASE_URL`` provides the role DSN; the test skips if it is unset.
"""

# ruff: noqa: ARG001 — ``migrated_engine`` is a dependency-ordering fixture param.
from __future__ import annotations

import os

import pytest
from persona_api.db.engine import rls_connection
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, ProgrammingError

pytestmark = pytest.mark.integration


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Engine:
    """A non-superuser (``persona_app``) engine for the RLS-under-test connection.

    Depends on ``migrated_engine`` so the migrated schema + grants exist first.
    Skips if ``APP_DATABASE_URL`` is unset (role provisioned out-of-band).
    """
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL (non-superuser role) not set; skipping RLS test")
    return create_engine(app_url.replace("+asyncpg", "+psycopg"))


def _seed_two_tenants_with_jobs(superuser_engine: Engine) -> None:
    # Seed as superuser (bypasses RLS) so both tenants' rows exist regardless of
    # policy — the test then proves the policy hides the other tenant's rows.
    with superuser_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email) VALUES "
                "('user_a','a@example.com'),('user_b','b@example.com')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO jobs (id, type, owner_id, idempotency_key, state) VALUES "
                "('ja','avatar_generation','user_a','avatar:pa:create','queued'),"
                "('jb','avatar_generation','user_b','avatar:pb:create','queued')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO jobs_archive "
                "(id, type, owner_id, idempotency_key, state, scheduled_at, created_at) VALUES "
                "('aa','avatar_generation','user_a','avatar:pa:old','succeeded',now(),now()),"
                "('ab','avatar_generation','user_b','avatar:pb:old','succeeded',now(),now())"
            )
        )


def test_jobs_isolated_per_tenant(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_two_tenants_with_jobs(migrated_engine)
    with rls_connection(app_engine, "user_a") as conn:
        owners = {r.owner_id for r in conn.execute(text("SELECT owner_id FROM jobs")).all()}
    assert owners == {"user_a"}, f"RLS leak on jobs: user_a saw {owners}"


def test_jobs_archive_isolated_per_tenant(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_two_tenants_with_jobs(migrated_engine)
    with rls_connection(app_engine, "user_b") as conn:
        owners = {r.owner_id for r in conn.execute(text("SELECT owner_id FROM jobs_archive")).all()}
    assert owners == {"user_b"}, f"RLS leak on jobs_archive: user_b saw {owners}"


def test_jobs_cross_tenant_write_blocked_by_with_check(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    # WITH CHECK must stop user_a from enqueuing a job owned by user_b.
    _seed_two_tenants_with_jobs(migrated_engine)
    with (
        rls_connection(app_engine, "user_a") as conn,
        pytest.raises(ProgrammingError),
    ):
        conn.execute(
            text(
                "INSERT INTO jobs (id, type, owner_id, idempotency_key) "
                "VALUES ('evil','avatar_generation','user_b','avatar:evil:create')"
            )
        )


def test_jobs_unset_user_sees_nothing_fail_closed(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    # No set_current_user → current_setting(...,true) is NULL → zero rows.
    _seed_two_tenants_with_jobs(migrated_engine)
    with app_engine.begin() as conn:
        rows = conn.execute(text("SELECT id FROM jobs")).all()
        archive_rows = conn.execute(text("SELECT id FROM jobs_archive")).all()
    assert rows == [], "jobs RLS must fail closed when app.current_user_id is unset"
    assert archive_rows == [], "jobs_archive RLS must fail closed when GUC is unset"


def test_duplicate_idempotency_key_rejected(migrated_engine: Engine) -> None:
    # The UNIQUE constraint is what makes ON CONFLICT (idempotency_key) DO NOTHING
    # a no-op; prove a duplicate raises (the queue layer turns it into a no-op).
    with migrated_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))
        conn.execute(
            text(
                "INSERT INTO jobs (id, type, owner_id, idempotency_key) "
                "VALUES ('j1','avatar_generation','user_a','dup:key')"
            )
        )
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (id, type, owner_id, idempotency_key) "
                "VALUES ('j2','avatar_generation','user_a','dup:key')"
            )
        )


def test_state_check_constraint_rejects_unknown_state(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (id, type, owner_id, idempotency_key, state) "
                "VALUES ('jx','avatar_generation','user_a','k','bogus')"
            )
        )


def test_claim_query_uses_partial_claim_index(migrated_engine: Engine) -> None:
    # Seed enough queued rows that the planner would consider the index, then
    # force index preference and assert the claim query plans onto idx_jobs_claim.
    with migrated_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))
        conn.execute(
            text(
                "INSERT INTO jobs (id, type, owner_id, idempotency_key, priority) "
                "SELECT 'job_' || g, 'avatar_generation', 'user_a', 'k:' || g, (g % 3) "
                "FROM generate_series(1, 500) AS g"
            )
        )
        conn.execute(text("ANALYZE jobs"))
        # SET LOCAL enable_seqscan=off proves the index *can* serve the predicate
        # (the production planner picks it once the queued backlog is non-trivial).
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                text(
                    "EXPLAIN SELECT id FROM jobs "
                    "WHERE state = 'queued' AND scheduled_at <= now() "
                    "ORDER BY priority DESC, scheduled_at "
                    "FOR UPDATE SKIP LOCKED LIMIT 10"
                )
            ).all()
        )
    assert "idx_jobs_claim" in plan, f"claim query did not use the partial index; plan:\n{plan}"
