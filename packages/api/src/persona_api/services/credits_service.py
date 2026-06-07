"""Credits counter + deduction (spec 08, T12, §5.5, D-08-6).

A stub counter (100,000 default; no payment integration — §2 out-of-scope) so
the architecture is forward-compatible. Deducted per successful turn AFTER the
stream completes (a failed/cancelled turn doesn't deduct — mirrors the
persist-after-final discipline) and a flat amount per authoring call (§11 risk).
Every deduction writes a ``credit_transactions`` row (the audit trail).

Both tables are RLS-scoped via ``user_id``, so all access runs under the
caller's tenant scope.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import insert, select, text, update

from persona_api.db.models import conversations as conversations_t
from persona_api.db.models import credit_transactions as credit_tx_t
from persona_api.db.models import credits as credits_t
from persona_api.db.models import turn_logs as turn_logs_t
from persona_api.errors import CreditsExhaustedError

if TYPE_CHECKING:
    from sqlalchemy import Engine

__all__ = [
    "LOW_BALANCE_THRESHOLD",
    "deduct",
    "ensure_balance",
    "get_balance",
    "list_turn_usage",
    "list_usage",
    "refund",
    "require_credits",
]

_DEFAULT_BALANCE = 100_000
# Below this threshold the web app surfaces a low-balance warning (D-11-12).
LOW_BALANCE_THRESHOLD = 10_000


def ensure_balance(*, rls_engine: Engine, user_id: str) -> int:
    """Return the user's balance, creating the row with the default on first use."""
    with rls_engine.begin() as conn:
        row = conn.execute(
            select(credits_t.c.balance).where(credits_t.c.user_id == user_id)
        ).first()
        if row is not None:
            return int(row[0])
        conn.execute(insert(credits_t).values(user_id=user_id, balance=_DEFAULT_BALANCE))
    return _DEFAULT_BALANCE


def get_balance(*, rls_engine: Engine, user_id: str) -> int:
    """Current balance (creates the default row if absent)."""
    return ensure_balance(rls_engine=rls_engine, user_id=user_id)


def require_credits(*, rls_engine: Engine, user_id: str) -> int:
    """Pre-flight credit check: raise :class:`CreditsExhaustedError` (→ 402) if
    the caller has no credits left. Returns the balance.

    Called at the **top** of every generation endpoint — chat, agentic runs,
    persona authoring and refinement — *before* the SSE stream / run starts.
    Raising inside the SSE generator yields the spec-08 "response already
    started" trap, so the pre-flight gate is the right place (D-11-12).
    The post-success ``deduct`` (D-08-6) is unchanged.
    """
    balance = ensure_balance(rls_engine=rls_engine, user_id=user_id)
    if balance <= 0:
        raise CreditsExhaustedError(
            "Your free credits are used up. Top-up coming soon — contact support.",
            context={"balance": str(balance)},
        )
    return balance


def deduct(*, rls_engine: Engine, user_id: str, amount: int, reason: str) -> int:
    """Deduct ``amount`` credits and record a transaction. Returns the new balance.

    Idempotent in spirit but not by key (v0.1): the caller deducts once per
    successful turn/authoring call. Allows the balance to go negative (a stub —
    we never block on exhaustion in v0.1; the counter exists for forward
    compatibility). Returns the post-deduction balance.
    """
    ensure_balance(rls_engine=rls_engine, user_id=user_id)
    with rls_engine.begin() as conn:
        new_balance = conn.execute(
            update(credits_t)
            .where(credits_t.c.user_id == user_id)
            .values(balance=credits_t.c.balance - amount, updated_at=text("now()"))
            .returning(credits_t.c.balance)
        ).scalar_one()
        conn.execute(
            insert(credit_tx_t).values(
                id=f"ctx_{uuid.uuid4().hex}",
                user_id=user_id,
                delta=-amount,
                reason=reason,
            )
        )
    return int(new_balance)


def refund(*, rls_engine: Engine, user_id: str, amount: int, reason: str) -> int:
    """Refund ``amount`` credits via a reverse-deduct ledger entry. Returns the new balance.

    Pattern (a) per D-15-X-credit-flow-semantics (spec 15 T13): writes
    ``INSERT INTO credit_transactions (delta=+amount, reason=...)`` and runs
    ``UPDATE credits SET balance = balance + amount`` in a single
    ``rls_engine.begin()`` transaction so the ledger and the running balance
    move atomically. Schema-compatible with the existing ``credit_transactions``
    table (``delta`` is ``Integer, nullable=False`` with no ``CheckConstraint``,
    so positive deltas are physically allowed — research §0.1); no Alembic
    migration required.

    Composed by ``persona_api.imagegen.service.generate`` (T15) on provider
    failure after the pre-deduct gate has fired, so a denial-of-wallet attacker
    cannot burn credits with parallel-fire failed generations
    (D-15-X-pre-deduct-credits; T17 is the binary proof). ``amount = 0`` is a
    no-op — no ledger row, no balance change, returns the current balance.
    """
    if amount == 0:
        return ensure_balance(rls_engine=rls_engine, user_id=user_id)
    ensure_balance(rls_engine=rls_engine, user_id=user_id)
    with rls_engine.begin() as conn:
        new_balance = conn.execute(
            update(credits_t)
            .where(credits_t.c.user_id == user_id)
            .values(balance=credits_t.c.balance + amount, updated_at=text("now()"))
            .returning(credits_t.c.balance)
        ).scalar_one()
        conn.execute(
            insert(credit_tx_t).values(
                id=f"ctx_{uuid.uuid4().hex}",
                user_id=user_id,
                delta=amount,
                reason=reason,
            )
        )
    return int(new_balance)


def list_usage(
    *, rls_engine: Engine, user_id: str, limit: int, offset: int
) -> list[dict[str, object]]:
    """The user's credit-transaction log (paginated)."""
    with rls_engine.begin() as conn:
        rows = (
            conn.execute(
                select(credit_tx_t)
                .where(credit_tx_t.c.user_id == user_id)
                .order_by(credit_tx_t.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def list_turn_usage(*, rls_engine: Engine, limit: int, offset: int) -> list[dict[str, object]]:
    """Per-turn token usage (§5.5) — turn_logs joined to the caller's
    conversations (RLS-scoped via conversations.owner_id), with the persona id."""
    with rls_engine.begin() as conn:
        rows = (
            conn.execute(
                select(
                    turn_logs_t,
                    conversations_t.c.persona_id.label("persona_id"),
                )
                .select_from(
                    turn_logs_t.join(
                        conversations_t,
                        turn_logs_t.c.conversation_id == conversations_t.c.id,
                    )
                )
                .order_by(turn_logs_t.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
