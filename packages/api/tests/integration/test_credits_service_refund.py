"""Integration tests for :func:`persona_api.services.credits_service.refund` (spec 15 T13).

``refund`` is the reverse-deduct ledger mechanic that lets the spec-15 image
service un-do the pre-deduct (D-15-X-pre-deduct-credits) when the provider
call fails: a single transaction writes ``credit_transactions(delta=+amount)``
and ``UPDATE credits SET balance = balance + amount`` so the ledger and the
running balance move atomically (D-15-X-credit-flow-semantics pattern (a)).

The schema is unchanged (research §0.1: ``credit_transactions.delta`` is
``Integer, nullable=False`` with no ``CheckConstraint`` — positive deltas
physically allowed); no Alembic migration was needed.

Tests run against a superuser ``pg_engine`` fixture (mirrors
``test_postgres_store.py``), which freshly creates the schema for each test —
RLS bypass via superuser is intentional: the per-engine RLS context listener
is exercised in the route-level integration tests (T18); this file pins the
ledger/balance arithmetic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona_api.db.models import credit_transactions as credit_tx_t
from persona_api.db.models import credits as credits_t
from persona_api.services import credits_service
from sqlalchemy import select, text

if TYPE_CHECKING:
    from sqlalchemy import Engine

pytestmark = pytest.mark.integration


_USER = "u_refund"


@pytest.fixture
def seeded_engine(pg_engine: Engine) -> Engine:
    """Insert the FK target user row so credits writes satisfy the constraint."""
    with pg_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
            {"i": _USER, "e": f"{_USER}@x.test"},
        )
    return pg_engine


def _balance(engine: Engine, user_id: str) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            select(credits_t.c.balance).where(credits_t.c.user_id == user_id)
        ).first()
    assert row is not None, "credits row missing for user"
    return int(row[0])


def _tx_rows(engine: Engine, user_id: str) -> list[dict[str, object]]:
    with engine.begin() as conn:
        rows = (
            conn.execute(
                select(credit_tx_t)
                .where(credit_tx_t.c.user_id == user_id)
                .order_by(credit_tx_t.c.created_at.asc(), credit_tx_t.c.id.asc())
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Happy path: deduct → refund restores balance and ledger has both rows
# ---------------------------------------------------------------------------


def test_refund_restores_balance_after_deduct(seeded_engine: Engine) -> None:
    """A pre-deduct followed by a refund nets to zero balance change."""
    start = credits_service.ensure_balance(rls_engine=seeded_engine, user_id=_USER)
    after_deduct = credits_service.deduct(
        rls_engine=seeded_engine, user_id=_USER, amount=200, reason="image_gen_pre"
    )
    assert after_deduct == start - 200

    after_refund = credits_service.refund(
        rls_engine=seeded_engine,
        user_id=_USER,
        amount=200,
        reason="image_gen_refund:ContentRejectedError",
    )
    assert after_refund == start, "refund must restore the pre-deduct balance"
    assert _balance(seeded_engine, _USER) == start


def test_refund_writes_positive_ledger_row(seeded_engine: Engine) -> None:
    """The ledger captures the refund as a ``+amount`` entry alongside the
    matching ``-amount`` deduct — the audit trail is symmetric and the running
    balance can be reconstructed by summing ``delta`` across the user's rows.
    """
    credits_service.ensure_balance(rls_engine=seeded_engine, user_id=_USER)
    credits_service.deduct(
        rls_engine=seeded_engine, user_id=_USER, amount=150, reason="image_gen_pre"
    )
    credits_service.refund(
        rls_engine=seeded_engine,
        user_id=_USER,
        amount=150,
        reason="image_gen_refund:ImageProviderError",
    )

    rows = _tx_rows(seeded_engine, _USER)
    assert len(rows) == 2
    deltas = [int(r["delta"]) for r in rows]
    assert deltas == [-150, 150], "ledger must show deduct then refund in order"
    reasons = [str(r["reason"]) for r in rows]
    assert reasons[0] == "image_gen_pre"
    assert reasons[1] == "image_gen_refund:ImageProviderError"
    # Running balance reconstructible from the ledger alone.
    assert sum(deltas) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_refund_zero_is_noop(seeded_engine: Engine) -> None:
    """``amount=0`` writes no ledger row and returns the current balance."""
    start = credits_service.ensure_balance(rls_engine=seeded_engine, user_id=_USER)
    returned = credits_service.refund(
        rls_engine=seeded_engine, user_id=_USER, amount=0, reason="noop"
    )
    assert returned == start
    rows = _tx_rows(seeded_engine, _USER)
    assert rows == [], "no-op refund must not write a ledger row"
    assert _balance(seeded_engine, _USER) == start


def test_refund_creates_credits_row_for_new_user(seeded_engine: Engine) -> None:
    """A refund on a user without a credits row first materialises the row
    via ``ensure_balance``, then applies the positive delta — pre-deduct may
    have happened on a freshly-created account in the same flow.
    """
    # No prior deduct → ensure_balance hasn't run yet for this user via the API.
    returned = credits_service.refund(
        rls_engine=seeded_engine,
        user_id=_USER,
        amount=42,
        reason="image_gen_refund:ImageProviderError",
    )
    # Default balance is 100_000 (credits_service._DEFAULT_BALANCE) + 42 refund.
    assert returned == 100_000 + 42
    assert _balance(seeded_engine, _USER) == 100_000 + 42
    rows = _tx_rows(seeded_engine, _USER)
    assert len(rows) == 1
    assert int(rows[0]["delta"]) == 42


def test_refund_is_independent_across_users(pg_engine: Engine) -> None:
    """Concurrent refunds on different users do not cross-contaminate balances
    or ledgers — the ``user_id`` predicate scopes both the UPDATE and the
    INSERT to the caller alone.
    """
    user_a = "u_refund_a"
    user_b = "u_refund_b"
    with pg_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
            {"i": user_a, "e": f"{user_a}@x.test"},
        )
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
            {"i": user_b, "e": f"{user_b}@x.test"},
        )

    credits_service.deduct(rls_engine=pg_engine, user_id=user_a, amount=300, reason="image_gen_pre")
    credits_service.deduct(rls_engine=pg_engine, user_id=user_b, amount=50, reason="image_gen_pre")
    # Refund only A; B is untouched.
    a_after = credits_service.refund(
        rls_engine=pg_engine,
        user_id=user_a,
        amount=300,
        reason="image_gen_refund:ContentRejectedError",
    )
    assert a_after == 100_000
    assert _balance(pg_engine, user_a) == 100_000
    assert _balance(pg_engine, user_b) == 100_000 - 50, "B must not be touched"

    a_rows = _tx_rows(pg_engine, user_a)
    b_rows = _tx_rows(pg_engine, user_b)
    assert [int(r["delta"]) for r in a_rows] == [-300, 300]
    assert [int(r["delta"]) for r in b_rows] == [-50]


def test_refund_returns_post_refund_balance(seeded_engine: Engine) -> None:
    """The return value is the updated balance after the refund lands — the
    callers (T15 image service) need this to log post-refund state.
    """
    credits_service.ensure_balance(rls_engine=seeded_engine, user_id=_USER)
    credits_service.deduct(
        rls_engine=seeded_engine, user_id=_USER, amount=1000, reason="image_gen_pre"
    )
    pre_refund_balance = _balance(seeded_engine, _USER)
    returned = credits_service.refund(
        rls_engine=seeded_engine,
        user_id=_USER,
        amount=400,
        reason="image_gen_refund:ImageProviderError",
    )
    assert returned == pre_refund_balance + 400
    assert returned == _balance(seeded_engine, _USER)


def test_refund_is_atomic_per_call(seeded_engine: Engine) -> None:
    """One call writes exactly one ledger row and one balance update —
    no partial states.
    """
    credits_service.ensure_balance(rls_engine=seeded_engine, user_id=_USER)
    before_rows = _tx_rows(seeded_engine, _USER)
    before_balance = _balance(seeded_engine, _USER)
    credits_service.refund(
        rls_engine=seeded_engine,
        user_id=_USER,
        amount=75,
        reason="image_gen_refund:ImageProviderError",
    )
    after_rows = _tx_rows(seeded_engine, _USER)
    assert len(after_rows) == len(before_rows) + 1
    assert int(after_rows[-1]["delta"]) == 75
    assert _balance(seeded_engine, _USER) == before_balance + 75
