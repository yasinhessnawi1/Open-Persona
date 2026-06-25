"""Migration + RLS + constraint + CAS-dedup tests for the task model (Spec A2, T4).

Runs against a real Postgres built by ``alembic upgrade head`` (so migration
``019_task_model`` and its RLS policies are present). Concerns:

1. **Tables + valid inserts** — a task and a checkpoint each insert cleanly with the
   server-side defaults.
2. **Constraints** — the state check, the ``wait_kind`` set-iff-WAITING check (the T2
   model_validator invariant at the DB), the non-negative ledger + checkpoint-seq checks.
3. **CAS-dedup (the durable half of A2-R-4)** — the UNIQUE(task_id, checkpoint_seq) rejects
   a raw duplicate; the full leg-handler write (ON CONFLICT INSERT + head CAS) makes a
   re-delivered leg a clean no-op (no double checkpoint, head unchanged), then advances on a
   genuine successor.
4. **RLS tenant isolation** (adversarial, the standing gate) — two tenants, each sees only
   its own rows under the ``persona_app`` non-superuser role; WITH CHECK blocks a
   cross-tenant insert; an unset GUC fails closed.

The non-superuser role is mandatory: superusers bypass RLS even under FORCE.
``APP_DATABASE_URL`` provides the role DSN; the RLS tests skip if it is unset.
"""

# ruff: noqa: ARG001 — ``migrated_engine`` is a dependency-ordering fixture param.
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from persona_api.db.engine import rls_connection
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, ProgrammingError

pytestmark = pytest.mark.integration

_CONTRACT = '\'{"goal": "find the cheapest fare"}\''


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Iterator[Engine]:
    """A non-superuser (``persona_app``) engine for the RLS-under-test connection."""
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL (non-superuser role) not set; skipping RLS test")
    engine = create_engine(app_url.replace("+asyncpg", "+psycopg"))
    yield engine
    engine.dispose()


def _seed_user_persona(engine: Engine, user_id: str, persona_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:u, :e)"),
            {"u": user_id, "e": f"{user_id}@example.com"},
        )
        conn.execute(
            text("INSERT INTO personas (id, owner_id, yaml) VALUES (:p, :o, 'name: x')"),
            {"p": persona_id, "o": user_id},
        )


def _insert_task(engine: Engine, *, task_id: str, owner: str, persona: str, **cols: str) -> None:
    extra_cols = "".join(f", {k}" for k in cols)
    extra_vals = "".join(f", :{k}" for k in cols)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO tasks (id, owner_id, persona_id, contract_json{extra_cols}) "
                f"VALUES (:t, :o, :p, {_CONTRACT}::jsonb{extra_vals})"
            ),
            {"t": task_id, "o": owner, "p": persona, **cols},
        )


# --- tables + valid inserts -------------------------------------------------


def test_task_inserts_with_defaults(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    _insert_task(migrated_engine, task_id="t1", owner="user_a", persona="persona_a")
    with migrated_engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT state, paused, wait_kind, ledger_model_micros, ledger_sandbox_micros, "
                "ledger_external_micros, head_checkpoint_seq, run_ids, schema_version, "
                "created_at, updated_at FROM tasks WHERE id = 't1'"
            )
        ).one()
    assert row.state == "defined"
    assert row.paused is False
    assert row.wait_kind is None
    assert row.ledger_model_micros == 0
    assert row.ledger_sandbox_micros == 0
    assert row.ledger_external_micros == 0
    assert row.head_checkpoint_seq is None
    assert row.run_ids == []
    assert row.schema_version == "1.0"
    assert row.created_at is not None
    assert row.updated_at is not None


def test_checkpoint_inserts(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    _insert_task(migrated_engine, task_id="t1", owner="user_a", persona="persona_a")
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO task_checkpoints "
                "(id, task_id, owner_id, checkpoint_seq, checkpoint_json, content_hash) "
                "VALUES ('cp1','t1','user_a',0,'{}'::jsonb,'hash0')"
            )
        )
        row = conn.execute(
            text("SELECT checkpoint_seq, schema_version FROM task_checkpoints WHERE id = 'cp1'")
        ).one()
    assert row.checkpoint_seq == 0
    assert row.schema_version == "1.0"


# --- constraints ------------------------------------------------------------


def test_state_check_rejects_unknown(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    with pytest.raises(IntegrityError):
        _insert_task(
            migrated_engine, task_id="t1", owner="user_a", persona="persona_a", state="bogus"
        )


def test_wait_kind_iff_waiting_rejects_active_with_kind(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    with pytest.raises(IntegrityError):
        _insert_task(
            migrated_engine,
            task_id="t1",
            owner="user_a",
            persona="persona_a",
            state="active",
            wait_kind="on_user",
        )


def test_wait_kind_iff_waiting_rejects_waiting_without_kind(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    with pytest.raises(IntegrityError):
        _insert_task(
            migrated_engine, task_id="t1", owner="user_a", persona="persona_a", state="waiting"
        )


def test_waiting_with_kind_inserts(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    _insert_task(
        migrated_engine,
        task_id="t1",
        owner="user_a",
        persona="persona_a",
        state="waiting",
        wait_kind="until_time",
    )
    with migrated_engine.begin() as conn:
        row = conn.execute(text("SELECT wait_kind FROM tasks WHERE id = 't1'")).one()
    assert row.wait_kind == "until_time"


def test_negative_ledger_rejected(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tasks (id, owner_id, persona_id, contract_json, ledger_model_micros) "
                f"VALUES ('t1','user_a','persona_a',{_CONTRACT}::jsonb,-1)"
            )
        )


def test_negative_checkpoint_seq_rejected(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    _insert_task(migrated_engine, task_id="t1", owner="user_a", persona="persona_a")
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO task_checkpoints "
                "(id, task_id, owner_id, checkpoint_seq, checkpoint_json, content_hash) "
                "VALUES ('cp1','t1','user_a',-1,'{}'::jsonb,'h')"
            )
        )


# --- CAS-dedup: the durable half of A2-R-4 ----------------------------------


def test_duplicate_checkpoint_seq_rejected_by_unique(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    _insert_task(migrated_engine, task_id="t1", owner="user_a", persona="persona_a")
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO task_checkpoints "
                "(id, task_id, owner_id, checkpoint_seq, checkpoint_json, content_hash) "
                "VALUES ('cp1','t1','user_a',0,'{}'::jsonb,'h0')"
            )
        )
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        # Same (task_id, seq), different id → UNIQUE(task_id, checkpoint_seq) rejects.
        conn.execute(
            text(
                "INSERT INTO task_checkpoints "
                "(id, task_id, owner_id, checkpoint_seq, checkpoint_json, content_hash) "
                "VALUES ('cp2','t1','user_a',0,'{}'::jsonb,'h0b')"
            )
        )


def _leg_cas_write(
    engine: Engine, *, task_id: str, owner: str, seq: int, predecessor: int | None
) -> tuple[int, int]:
    """Simulate the leg handler's idempotent checkpoint write (one transaction).

    Returns (checkpoint_insert_rowcount, head_update_rowcount). A re-delivery returns
    (0, 0) — the ON CONFLICT no-ops the checkpoint and the head CAS matches no row.
    """
    with engine.begin() as conn:
        ins = conn.execute(
            text(
                "INSERT INTO task_checkpoints "
                "(id, task_id, owner_id, checkpoint_seq, checkpoint_json, content_hash) "
                "VALUES (gen_random_uuid()::text, :t, :o, :s, '{}'::jsonb, :h) "
                "ON CONFLICT (task_id, checkpoint_seq) DO NOTHING"
            ),
            {"t": task_id, "o": owner, "s": seq, "h": f"hash-{seq}"},
        )
        upd = conn.execute(
            text(
                "UPDATE tasks SET head_checkpoint_seq = :s, updated_at = now() "
                "WHERE id = :t AND head_checkpoint_seq IS NOT DISTINCT FROM CAST(:p AS integer)"
            ),
            {"s": seq, "t": task_id, "p": predecessor},
        )
    return ins.rowcount, upd.rowcount


def test_checkpoint_cas_write_is_idempotent_under_redelivery(migrated_engine: Engine) -> None:
    _seed_user_persona(migrated_engine, "user_a", "persona_a")
    _insert_task(migrated_engine, task_id="t1", owner="user_a", persona="persona_a")

    # First delivery of leg 0 (predecessor = NULL head): both statements affect one row.
    assert _leg_cas_write(
        migrated_engine, task_id="t1", owner="user_a", seq=0, predecessor=None
    ) == (1, 1)

    # Re-delivery of the SAME leg: checkpoint no-ops (ON CONFLICT), head CAS matches no row
    # (head is now 0, not NULL) → a clean no-op. No double checkpoint, head unchanged.
    assert _leg_cas_write(
        migrated_engine, task_id="t1", owner="user_a", seq=0, predecessor=None
    ) == (0, 0)

    with migrated_engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM task_checkpoints WHERE task_id = 't1'")
        ).scalar_one()
        head = conn.execute(
            text("SELECT head_checkpoint_seq FROM tasks WHERE id = 't1'")
        ).scalar_one()
    assert count == 1
    assert head == 0

    # A genuine successor (leg 1, predecessor = 0) advances exactly once.
    assert _leg_cas_write(migrated_engine, task_id="t1", owner="user_a", seq=1, predecessor=0) == (
        1,
        1,
    )
    with migrated_engine.begin() as conn:
        head = conn.execute(
            text("SELECT head_checkpoint_seq FROM tasks WHERE id = 't1'")
        ).scalar_one()
    assert head == 1


# --- RLS tenant isolation (adversarial) -------------------------------------


def _seed_two_tenants(engine: Engine) -> None:
    _seed_user_persona(engine, "user_a", "persona_a")
    _seed_user_persona(engine, "user_b", "persona_b")
    _insert_task(engine, task_id="ta", owner="user_a", persona="persona_a")
    _insert_task(engine, task_id="tb", owner="user_b", persona="persona_b")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO task_checkpoints "
                "(id, task_id, owner_id, checkpoint_seq, checkpoint_json, content_hash) VALUES "
                "('cpa','ta','user_a',0,'{}'::jsonb,'ha'),"
                "('cpb','tb','user_b',0,'{}'::jsonb,'hb')"
            )
        )


def test_tasks_isolated_per_tenant(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_two_tenants(migrated_engine)
    with rls_connection(app_engine, "user_a") as conn:
        owners = {r.owner_id for r in conn.execute(text("SELECT owner_id FROM tasks")).all()}
    assert owners == {"user_a"}, f"RLS leak on tasks: user_a saw {owners}"


def test_checkpoints_isolated_per_tenant(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_two_tenants(migrated_engine)
    with rls_connection(app_engine, "user_a") as conn:
        owners = {
            r.owner_id for r in conn.execute(text("SELECT owner_id FROM task_checkpoints")).all()
        }
    assert owners == {"user_a"}, f"RLS leak on task_checkpoints: user_a saw {owners}"


def test_tasks_cross_tenant_write_blocked_by_with_check(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed_two_tenants(migrated_engine)
    with (
        rls_connection(app_engine, "user_a") as conn,
        pytest.raises(ProgrammingError),
    ):
        conn.execute(
            text(
                "INSERT INTO tasks (id, owner_id, persona_id, contract_json) "
                f"VALUES ('evil','user_b','persona_b',{_CONTRACT}::jsonb)"
            )
        )


def test_tasks_unset_user_sees_nothing_fail_closed(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed_two_tenants(migrated_engine)
    with app_engine.begin() as conn:
        rows = conn.execute(text("SELECT id FROM tasks")).all()
    assert rows == [], "tasks RLS must fail closed when app.current_user_id is unset"
