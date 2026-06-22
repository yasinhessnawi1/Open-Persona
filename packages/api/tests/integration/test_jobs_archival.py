"""Queue-hygiene archival tests (Spec A0, T10) — D-A0-4.

Against real Postgres. Proves terminal jobs age out of the hot ``jobs`` table into
cold ``jobs_archive`` (keeping the claim path small), recently-terminal + non-
terminal jobs stay hot, archival is race-safe under concurrent sweeps, retention
purges old archive rows, and the worker's maintenance sweep runs all three.
"""

# ruff: noqa: SLF001, ARG001 — private engine access + fixture-ordering param.
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from persona.jobs import JobRegistry
from persona_api.jobs import JobQueue, Worker
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Engine:
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set; skipping archival test")
    return create_engine(app_url.replace("+asyncpg", "+psycopg"))


@pytest.fixture
def seeded(migrated_engine: Engine) -> Engine:
    with migrated_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))
    return migrated_engine


def _insert_job(engine: Engine, jid: str, *, state: str, age_seconds: float) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (id, type, owner_id, idempotency_key, state, created_at) "
                "VALUES (:id, 't', 'user_a', :k, :state, now() - make_interval(secs => :age))"
            ),
            {"id": jid, "k": jid, "state": state, "age": age_seconds},
        )


def _counts(engine: Engine) -> tuple[int, int]:
    with engine.begin() as conn:
        hot = conn.execute(text("SELECT count(*) FROM jobs")).scalar_one()
        cold = conn.execute(text("SELECT count(*) FROM jobs_archive")).scalar_one()
    return hot, cold


def test_archive_terminal_moves_only_old_terminal_jobs(seeded: Engine) -> None:
    _insert_job(seeded, "old_done", state="succeeded", age_seconds=3600)  # archive
    _insert_job(seeded, "old_dead", state="dead", age_seconds=3600)  # archive
    _insert_job(seeded, "new_done", state="succeeded", age_seconds=1)  # stays (recent)
    _insert_job(seeded, "queued", state="queued", age_seconds=3600)  # stays (non-terminal)
    _insert_job(seeded, "running", state="running", age_seconds=3600)  # stays (non-terminal)
    queue = JobQueue(seeded)

    moved = queue.archive_terminal(older_than=datetime.now(UTC) - timedelta(seconds=60))

    assert moved == 2
    hot, cold = _counts(seeded)
    assert hot == 3  # new_done, queued, running
    assert cold == 2  # old_done, old_dead
    with seeded.begin() as conn:
        archived = {r[0] for r in conn.execute(text("SELECT id FROM jobs_archive")).all()}
    assert archived == {"old_done", "old_dead"}


def test_archive_preserves_row_fields(seeded: Engine) -> None:
    with seeded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (id, type, owner_id, idempotency_key, state, last_error, "
                "created_at) VALUES "
                "('j','avatar','user_a','k','dead','boom', now()-interval '1 hour')"
            )
        )
    JobQueue(seeded).archive_terminal(older_than=datetime.now(UTC) - timedelta(seconds=60))
    with seeded.begin() as conn:
        row = conn.execute(
            text(
                "SELECT type, owner_id, state, last_error, archived_at "
                "FROM jobs_archive WHERE id='j'"
            )
        ).one()
    assert row.type == "avatar"
    assert row.owner_id == "user_a"
    assert row.state == "dead"
    assert row.last_error == "boom"
    assert row.archived_at is not None


def test_archive_is_race_safe_under_concurrent_sweeps(seeded: Engine) -> None:
    for i in range(40):
        _insert_job(seeded, f"j{i:02d}", state="succeeded", age_seconds=3600)
    queue = JobQueue(seeded)
    cutoff = datetime.now(UTC) - timedelta(seconds=60)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = [
            pool.submit(queue.archive_terminal, older_than=cutoff, limit=50) for _ in range(3)
        ]
        total = sum(r.result() for r in results)

    assert total == 40, "every terminal job archived exactly once (no double, no loss)"
    hot, cold = _counts(seeded)
    assert hot == 0
    assert cold == 40


def test_purge_archive_deletes_old_rows(seeded: Engine) -> None:
    with seeded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs_archive (id, type, owner_id, idempotency_key, state, "
                "scheduled_at, created_at, archived_at) VALUES "
                "('old','t','user_a','k1','dead', now(), now(), now()-interval '40 days'),"
                "('new','t','user_a','k2','dead', now(), now(), now()-interval '1 day')"
            )
        )
    purged = JobQueue(seeded).purge_archive(older_than=datetime.now(UTC) - timedelta(days=30))
    assert purged == 1
    with seeded.begin() as conn:
        remaining = {r[0] for r in conn.execute(text("SELECT id FROM jobs_archive")).all()}
    assert remaining == {"new"}


def test_worker_maintenance_reclaims_archives_purges(seeded: Engine, app_engine: Engine) -> None:
    # An expired-lease job (reclaim), an old terminal job (archive), an old archive
    # row (purge) — one sweep handles all three.
    _insert_job(seeded, "old_done", state="succeeded", age_seconds=3600)
    with seeded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (id, type, owner_id, idempotency_key, state, locked_by, "
                "lease_expires_at) VALUES ('stuck','t','user_a','ks','running','wDead', "
                "now()-interval '1 minute')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO jobs_archive (id, type, owner_id, idempotency_key, state, "
                "scheduled_at, created_at, archived_at) VALUES "
                "('oldarch','t','user_a','ka','dead', now(), now(), now()-interval '40 days')"
            )
        )
    worker = Worker(
        dispatch_engine=seeded,
        rls_engine=app_engine,
        registry=JobRegistry(),
        worker_id="w1",
        archive_after_seconds=60.0,
        archive_retention_seconds=30 * 86_400.0,
    )

    worker.run_maintenance()

    with seeded.begin() as conn:
        stuck_state = conn.execute(text("SELECT state FROM jobs WHERE id='stuck'")).scalar_one()
        old_done_hot = conn.execute(
            text("SELECT count(*) FROM jobs WHERE id='old_done'")
        ).scalar_one()
        old_done_cold = conn.execute(
            text("SELECT count(*) FROM jobs_archive WHERE id='old_done'")
        ).scalar_one()
        oldarch = conn.execute(
            text("SELECT count(*) FROM jobs_archive WHERE id='oldarch'")
        ).scalar_one()
    assert stuck_state == "queued"  # reclaimed
    assert old_done_hot == 0  # archived out of hot
    assert old_done_cold == 1  # ...into cold
    assert oldarch == 0  # purged
