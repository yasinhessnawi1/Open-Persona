"""Cadence caps: priority-bypass + chatter-batches-to-digest (Spec A3, T12; A3-D-4).

Against real Postgres (the atomic conditional upsert is the cap). The non-negotiable property is
**priority-bypass**: a persona that has exhausted its per-day chatter cap must STILL deliver an
approval (and a failure) immediately — otherwise the budget "extend?" approval would be capped
and a budget-halted task would deadlock.
"""

# ruff: noqa: ARG001 — ``migrated_engine`` is a dependency-ordering fixture param.
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from persona_api.approvals import CadenceDecision, CadenceGate, MessagePriority
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


@pytest.fixture
def app_engine(migrated_engine: Engine) -> Iterator[Engine]:
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL (non-superuser role) not set; skipping cadence test")
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


def _exhaust_chatter(gate: CadenceGate, owner: str, persona: str, cap: int) -> None:
    for _ in range(cap):
        decision = gate.admit(owner, persona, MessagePriority.PROGRESS, now=_NOW)
        assert decision is CadenceDecision.DELIVER


# --- chatter cap ------------------------------------------------------------


def test_chatter_delivers_under_cap_then_digests(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    gate = CadenceGate(app_engine, daily_cap=3)
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DELIVER
    )
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DELIVER
    )
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DELIVER
    )
    # The 4th chatter message over a cap of 3 batches to the digest.
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DIGEST
    )


# --- the non-negotiable: priority bypass (the deadlock guard) ----------------


def test_exhausted_cap_still_delivers_approval_and_failure(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    gate = CadenceGate(app_engine, daily_cap=2)
    _exhaust_chatter(gate, "user_a", "persona_a", 2)
    # Chatter is now capped...
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DIGEST
    )
    # ...but an APPROVAL (e.g. the budget "extend?" ask) STILL delivers — else a budget-halted
    # task could never be extended (deadlock). Same for a FAILURE account and SAFETY messages.
    assert gate.admit("user_a", "persona_a", MessagePriority.APPROVAL, now=_NOW) is (
        CadenceDecision.DELIVER
    )
    assert gate.admit("user_a", "persona_a", MessagePriority.FAILURE, now=_NOW) is (
        CadenceDecision.DELIVER
    )
    assert gate.admit("user_a", "persona_a", MessagePriority.SAFETY, now=_NOW) is (
        CadenceDecision.DELIVER
    )


def test_priority_messages_do_not_consume_the_chatter_budget(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    gate = CadenceGate(app_engine, daily_cap=2)
    # Many approvals/failures first — they must not eat the chatter budget.
    for _ in range(5):
        gate.admit("user_a", "persona_a", MessagePriority.APPROVAL, now=_NOW)
        gate.admit("user_a", "persona_a", MessagePriority.FAILURE, now=_NOW)
    # The 2 chatter messages still deliver (the budget is intact), the 3rd digests.
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DELIVER
    )
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DELIVER
    )
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DIGEST
    )


# --- scope: per persona / per day / per owner -------------------------------


def test_cap_is_per_persona_and_per_day(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    _seed(migrated_engine, "user_a", "persona_b")
    gate = CadenceGate(app_engine, daily_cap=1)
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DELIVER
    )
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DIGEST
    )
    # A different persona has its own budget...
    assert gate.admit("user_a", "persona_b", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DELIVER
    )
    # ...and the next day resets persona_a's budget.
    tomorrow = _NOW + timedelta(days=1)
    assert gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=tomorrow) is (
        CadenceDecision.DELIVER
    )


def test_cap_is_owner_scoped(migrated_engine: Engine, app_engine: Engine) -> None:
    _seed(migrated_engine, "user_a", "persona_a")
    _seed(migrated_engine, "user_b", "persona_b")
    gate = CadenceGate(app_engine, daily_cap=1)
    gate.admit("user_a", "persona_a", MessagePriority.PROGRESS, now=_NOW)  # user_a exhausts
    # user_b's counter is independent (RLS-scoped) — a separate budget.
    assert gate.admit("user_b", "persona_b", MessagePriority.PROGRESS, now=_NOW) is (
        CadenceDecision.DELIVER
    )
