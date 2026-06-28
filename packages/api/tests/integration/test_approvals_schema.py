"""Migration + RLS + constraint tests for the A3 approval tables (Spec A3, T5).

Runs against a real Postgres built by ``alembic upgrade head`` (so migration
``022_approvals_permissions`` and its RLS policies are present). Concerns:

1. **Tables + valid inserts** — a proposal and a decision insert cleanly with the
   server-side defaults (``status='pending'``, timestamps).
2. **Constraints** — the status / type checks; the **one-pending partial-unique index**
   (``(task_id) WHERE status='pending'``) makes a second open proposal on a task impossible
   and frees once the first leaves ``pending``; the **composite FK** pins a decision's
   denormalized ``owner_id`` to its proposal's owner (a cross-owner decision is impossible).
3. **RLS tenant isolation** (adversarial, the standing gate) — two tenants, each sees only
   its own proposals/decisions under the ``persona_app`` non-superuser role; WITH CHECK blocks
   a cross-tenant insert; an unset GUC fails closed.

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

_CONTRACT = '\'{"goal": "book the cheapest fare"}\''


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Iterator[Engine]:
    """A non-superuser (``persona_app``) engine for the RLS-under-test connection."""
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL (non-superuser role) not set; skipping RLS test")
    engine = create_engine(app_url.replace("+asyncpg", "+psycopg"))
    yield engine
    engine.dispose()


def _seed_user_persona_task(engine: Engine, user: str, persona: str, task: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:u, :e)"),
            {"u": user, "e": f"{user}@example.com"},
        )
        conn.execute(
            text("INSERT INTO personas (id, owner_id, yaml) VALUES (:p, :o, 'name: x')"),
            {"p": persona, "o": user},
        )
        conn.execute(
            text(
                "INSERT INTO tasks (id, owner_id, persona_id, contract_json) "
                f"VALUES (:t, :o, :p, {_CONTRACT}::jsonb)"
            ),
            {"t": task, "o": user, "p": persona},
        )


def _insert_proposal(
    engine: Engine,
    *,
    pid: str,
    owner: str,
    task: str,
    persona: str,
    status: str = "pending",
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO approval_proposals "
                "(id, owner_id, task_id, persona_id, tool_name, description, status) "
                "VALUES (:i, :o, :t, :p, 'send_email', 'Send an email to bob', :s)"
            ),
            {"i": pid, "o": owner, "t": task, "p": persona, "s": status},
        )


def _insert_decision(engine: Engine, *, did: str, owner: str, proposal: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO approval_decisions "
                "(id, owner_id, proposal_id, type, verbatim_reply, channel) "
                "VALUES (:i, :o, :pr, 'approve', 'yes send it', 'telegram')"
            ),
            {"i": did, "o": owner, "pr": proposal},
        )


# --- tables + valid inserts -------------------------------------------------


def test_proposal_inserts_with_defaults(migrated_engine: Engine) -> None:
    _seed_user_persona_task(migrated_engine, "user_a", "persona_a", "t1")
    _insert_proposal(migrated_engine, pid="p1", owner="user_a", task="t1", persona="persona_a")
    with migrated_engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT status, arguments_json, categories_json, created_at, updated_at "
                "FROM approval_proposals WHERE id = 'p1'"
            )
        ).one()
    assert row.status == "pending"
    assert row.arguments_json == {}
    assert row.categories_json == []
    assert row.created_at is not None
    assert row.updated_at is not None


def test_decision_inserts(migrated_engine: Engine) -> None:
    _seed_user_persona_task(migrated_engine, "user_a", "persona_a", "t1")
    _insert_proposal(migrated_engine, pid="p1", owner="user_a", task="t1", persona="persona_a")
    _insert_decision(migrated_engine, did="d1", owner="user_a", proposal="p1")
    with migrated_engine.begin() as conn:
        row = conn.execute(
            text("SELECT type, verbatim_reply, channel FROM approval_decisions WHERE id = 'd1'")
        ).one()
    assert row.type == "approve"
    assert row.verbatim_reply == "yes send it"
    assert row.channel == "telegram"


# --- constraints ------------------------------------------------------------


def test_status_check_rejects_unknown(migrated_engine: Engine) -> None:
    _seed_user_persona_task(migrated_engine, "user_a", "persona_a", "t1")
    with pytest.raises(IntegrityError):
        _insert_proposal(
            migrated_engine,
            pid="p1",
            owner="user_a",
            task="t1",
            persona="persona_a",
            status="bogus",
        )


def test_decision_type_check_rejects_unknown(migrated_engine: Engine) -> None:
    _seed_user_persona_task(migrated_engine, "user_a", "persona_a", "t1")
    _insert_proposal(migrated_engine, pid="p1", owner="user_a", task="t1", persona="persona_a")
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO approval_decisions "
                "(id, owner_id, proposal_id, type, verbatim_reply, channel) "
                "VALUES ('d1','user_a','p1','bogus','x','telegram')"
            )
        )


def test_one_pending_per_task_rejects_second_open_proposal(migrated_engine: Engine) -> None:
    _seed_user_persona_task(migrated_engine, "user_a", "persona_a", "t1")
    _insert_proposal(migrated_engine, pid="p1", owner="user_a", task="t1", persona="persona_a")
    # A second PENDING proposal on the same task is structurally impossible.
    with pytest.raises(IntegrityError):
        _insert_proposal(migrated_engine, pid="p2", owner="user_a", task="t1", persona="persona_a")


def test_one_pending_frees_once_resolved(migrated_engine: Engine) -> None:
    _seed_user_persona_task(migrated_engine, "user_a", "persona_a", "t1")
    _insert_proposal(migrated_engine, pid="p1", owner="user_a", task="t1", persona="persona_a")
    # Resolving the first (out of 'pending') frees the one-pending slot for the next gate.
    with migrated_engine.begin() as conn:
        conn.execute(text("UPDATE approval_proposals SET status='consumed' WHERE id='p1'"))
    _insert_proposal(migrated_engine, pid="p2", owner="user_a", task="t1", persona="persona_a")
    with migrated_engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM approval_proposals WHERE task_id='t1'")
        ).scalar_one()
    assert count == 2


def test_composite_fk_pins_decision_owner_to_proposal_owner(migrated_engine: Engine) -> None:
    # The denormalization invariant, enforced structurally: a decision whose owner_id differs
    # from its proposal's owner cannot be written (the (owner_id, proposal_id) pair has no
    # matching proposal). FK validation is owner-independent, so this holds even for a superuser.
    _seed_user_persona_task(migrated_engine, "user_a", "persona_a", "ta")
    _seed_user_persona_task(migrated_engine, "user_b", "persona_b", "tb")
    _insert_proposal(migrated_engine, pid="pb", owner="user_b", task="tb", persona="persona_b")
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        # owner_id='user_a' but proposal 'pb' is user_b's → (user_a, pb) matches no proposal.
        conn.execute(
            text(
                "INSERT INTO approval_decisions "
                "(id, owner_id, proposal_id, type, verbatim_reply, channel) "
                "VALUES ('d1','user_a','pb','approve','x','telegram')"
            )
        )


# --- RLS tenant isolation (adversarial) -------------------------------------


def _seed_two_tenants(engine: Engine) -> None:
    _seed_user_persona_task(engine, "user_a", "persona_a", "ta")
    _seed_user_persona_task(engine, "user_b", "persona_b", "tb")
    _insert_proposal(engine, pid="pa", owner="user_a", task="ta", persona="persona_a")
    _insert_proposal(engine, pid="pb", owner="user_b", task="tb", persona="persona_b")
    _insert_decision(engine, did="da", owner="user_a", proposal="pa")
    _insert_decision(engine, did="db", owner="user_b", proposal="pb")


def test_proposals_isolated_per_tenant(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_two_tenants(migrated_engine)
    with rls_connection(app_engine, "user_a") as conn:
        owners = {
            r.owner_id for r in conn.execute(text("SELECT owner_id FROM approval_proposals")).all()
        }
    assert owners == {"user_a"}, f"RLS leak on approval_proposals: user_a saw {owners}"


def test_decisions_isolated_per_tenant(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed_two_tenants(migrated_engine)
    with rls_connection(app_engine, "user_a") as conn:
        owners = {
            r.owner_id for r in conn.execute(text("SELECT owner_id FROM approval_decisions")).all()
        }
    assert owners == {"user_a"}, f"RLS leak on approval_decisions: user_a saw {owners}"


def test_proposal_cross_tenant_write_blocked_by_with_check(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed_two_tenants(migrated_engine)
    with (
        rls_connection(app_engine, "user_a") as conn,
        pytest.raises(ProgrammingError),
    ):
        conn.execute(
            text(
                "INSERT INTO approval_proposals "
                "(id, owner_id, task_id, persona_id, tool_name, description) "
                "VALUES ('evil','user_b','tb','persona_b','send_email','x')"
            )
        )


def test_proposals_unset_user_sees_nothing_fail_closed(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed_two_tenants(migrated_engine)
    with app_engine.begin() as conn:
        rows = conn.execute(text("SELECT id FROM approval_proposals")).all()
    assert rows == [], "approval_proposals RLS must fail closed when app.current_user_id is unset"
