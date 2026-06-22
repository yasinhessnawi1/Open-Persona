"""Adversarial RLS + end-to-end worker tests (Spec A0, T4) — the hard gates.

Against real Postgres. The two non-negotiable gates:

1. **Handler-engine isolation** — a handler's only DB access is the owner-scoped
   ``WorkerJobContext.connection()`` (``persona_app`` + owner GUC). It never
   receives the cross-tenant dispatch engine.
2. **Adversarial cross-tenant** — a job for user_a whose handler reads ALL
   personas sees ONLY user_a's persona; user_b's rows return ZERO. The RLS choke
   point is structural, proven here, not asserted.

Plus the worker composition runs a trivial registered handler end-to-end, and the
health probes report correctly.
"""

# ruff: noqa: ARG001, ARG002 — fixture-ordering params + protocol-required handler args.
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from persona.jobs import JobPayload, JobRegistry, JobTypeSpec
from persona_api.jobs import JobQueue, Worker, WorkerJobContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from persona.jobs import JobContext

pytestmark = pytest.mark.integration


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Engine:
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL (non-superuser role) not set; skipping worker RLS test")
    return create_engine(app_url.replace("+asyncpg", "+psycopg"))


def _seed_two_tenants(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email) VALUES "
                "('user_a','a@example.com'),('user_b','b@example.com')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO personas (id, owner_id, yaml) VALUES "
                "('pa','user_a','name: a'),('pb','user_b','name: b')"
            )
        )


class _ReadPersonasPayload(JobPayload):
    marker: str = "m"


class _ReadAllPersonasHandler:
    """Adversarial handler: tries to read EVERY persona via its owner-scoped conn."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.context_attrs: set[str] = set()

    async def handle(self, payload: _ReadPersonasPayload, context: JobContext) -> None:
        # Structural isolation: the context exposes only owner-scoped access.
        self.context_attrs = {a for a in dir(context) if not a.startswith("_")}
        with context.connection() as conn:
            self.seen = [r.id for r in conn.execute(text("SELECT id FROM personas")).all()]


def _worker_with_handler(dispatch_engine: Engine, rls_engine: Engine, handler: object) -> Worker:
    registry = JobRegistry(
        [
            JobTypeSpec(
                type="read_personas",
                payload_model=_ReadPersonasPayload,
                handler=handler,  # type: ignore[arg-type]
                idempotency_key=lambda p: f"read:{p.marker}",
            )
        ]
    )
    return Worker(
        dispatch_engine=dispatch_engine,
        rls_engine=rls_engine,
        registry=registry,
        worker_id="w-test",
    )


def test_handler_sees_only_owner_rows_adversarial(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed_two_tenants(migrated_engine)
    handler = _ReadAllPersonasHandler()
    # dispatch = superuser (cross-tenant claim); rls = persona_app (owner-scoped).
    worker = _worker_with_handler(migrated_engine, app_engine, handler)
    JobQueue(migrated_engine).enqueue(
        type="read_personas",
        owner_id="user_a",
        payload={"marker": "m1"},
        idempotency_key="read:m1",
    )

    import asyncio

    ran = asyncio.run(worker.run_once())

    assert ran == 1
    # THE GATE: the handler ran under user_a's scope and saw ONLY user_a's persona.
    assert handler.seen == ["pa"], f"RLS choke point leaked cross-tenant rows: {handler.seen}"
    assert "pb" not in handler.seen


def test_handler_context_exposes_no_dispatch_engine(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed_two_tenants(migrated_engine)
    handler = _ReadAllPersonasHandler()
    worker = _worker_with_handler(migrated_engine, app_engine, handler)
    JobQueue(migrated_engine).enqueue(
        type="read_personas",
        owner_id="user_a",
        payload={"marker": "m1"},
        idempotency_key="read:m1",
    )

    import asyncio

    asyncio.run(worker.run_once())

    # Structural: the context surface is owner-scoped affordances only — no engine,
    # no dispatch handle, nothing cross-tenant reachable.
    assert handler.context_attrs == {"owner_id", "job_id", "connection", "meter"}


def test_worker_runs_trivial_handler_end_to_end(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES ('user_a','a@example.com')"))

    class _TrivialHandler:
        def __init__(self) -> None:
            self.ran = False
            self.owner: str | None = None

        async def handle(self, payload: _ReadPersonasPayload, context: JobContext) -> None:
            self.ran = True
            self.owner = context.owner_id

    handler = _TrivialHandler()
    worker = _worker_with_handler(migrated_engine, app_engine, handler)
    job = JobQueue(migrated_engine).enqueue(
        type="read_personas",
        owner_id="user_a",
        payload={"marker": "m1"},
        idempotency_key="read:m1",
    )
    assert job is not None

    import asyncio

    assert asyncio.run(worker.run_once()) == 1
    assert handler.ran
    assert handler.owner == "user_a"
    with migrated_engine.begin() as conn:
        state = conn.execute(text("SELECT state FROM jobs WHERE id = :i"), {"i": job.id}).scalar()
    assert state == "succeeded"


def test_worker_context_is_owner_scoped_directly(
    app_engine: Engine, migrated_engine: Engine
) -> None:
    # The WorkerJobContext yields a connection bound to its owner (defence-in-depth
    # check, independent of the executor).
    _seed_two_tenants(migrated_engine)
    ctx = WorkerJobContext(owner_id="user_b", rls_engine=app_engine, job_id="job-x", job_type="t")
    assert ctx.owner_id == "user_b"
    with ctx.connection() as conn:
        seen = [r.id for r in conn.execute(text("SELECT id FROM personas")).all()]
    assert seen == ["pb"]


def test_worker_health_probes(migrated_engine: Engine, app_engine: Engine) -> None:
    worker = _worker_with_handler(migrated_engine, app_engine, _ReadAllPersonasHandler())
    assert worker.livez() == ("ok", 200)
    assert worker.healthz() == ("ok", 200)


def test_worker_healthz_reports_503_when_db_unreachable(app_engine: Engine) -> None:
    from persona_api.db.engine import create_db_engine

    dead = create_db_engine("postgresql+psycopg://nobody:nobody@localhost:1/none")
    worker = _worker_with_handler(dead, app_engine, _ReadAllPersonasHandler())
    assert worker.healthz() == ("db_unreachable", 503)
    assert worker.livez() == ("ok", 200)  # liveness is DB-free
