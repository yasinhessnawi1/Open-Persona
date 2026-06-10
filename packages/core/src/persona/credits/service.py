"""Credits counter + deduction (relocated from ``persona_api.services.credits_service``).

Originally landed at spec 08, T12, §5.5, D-08-6; refunds added at spec 15 T13
(D-15-X-credit-flow-semantics). Relocated to persona-core at Spec 19 L6c
(D-19-X-credits-service-domain-relocation) so persona-voice can consume the
same surface without taking a persona-api dependency — the voice surface is
latency-critical (R-V1-1) and cannot afford an HTTP/RPC hop to the API.

The implementation is verbatim from the prior ``persona_api.services.credits_service``
with one structural change: the SQLAlchemy ``Table`` objects are defined here
on a private :class:`MetaData` (mirroring the pattern used by
:mod:`persona.stores.postgres` for ``memory_chunks``). Column names/types match
the api-owned canonical schema in ``persona_api.db.models``; the existing
api-side route integration tests act as the contract guard that the two views
agree.

A stub counter (100,000 default; no payment integration — spec 08 §2 out-of-scope)
so the architecture is forward-compatible. Deducted per successful turn AFTER
the stream completes (a failed/cancelled turn doesn't deduct — mirrors the
persist-after-final discipline) and a flat amount per authoring call (§11 risk).
Every deduction writes a ``credit_transactions`` row (the audit trail).

Both tables are RLS-scoped via ``user_id``, so all access runs under the
caller's tenant scope.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    Table,
    Text,
    insert,
    select,
    text,
    update,
)

from persona.errors import CreditsExhaustedError

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


# Module-private minimal table views. persona-core cannot import the api
# package, so we mirror the api-owned column shapes here (D-07-2 pattern;
# `stores/postgres.py` does the same for memory_chunks). The api-side route
# integration tests double as the contract guard that drift is caught early.
_md = MetaData()

_credits_t = Table(
    "credits",
    _md,
    Column("user_id", Text, primary_key=True),
    Column("balance", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

_credit_tx_t = Table(
    "credit_transactions",
    _md,
    Column("id", Text, primary_key=True),
    Column("user_id", Text, nullable=False),
    Column("delta", Integer, nullable=False),
    Column("reason", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

_conversations_t = Table(
    "conversations",
    _md,
    Column("id", Text, primary_key=True),
    Column("owner_id", Text, nullable=False),
    Column("persona_id", Text, nullable=False),
)

_turn_logs_t = Table(
    # Local minimal view mirrors the api-owned canonical schema at
    # packages/api/src/persona_api/db/models.py:244-272 (v0.1.1: column set
    # restored to include latency_ms / cost_cents / tool_calls / skill_used /
    # history_compacted so list_turn_usage's select() resolves all the
    # fields the /v1/me/usage route consumes).
    "turn_logs",
    _md,
    Column("id", Text, primary_key=True),
    Column("conversation_id", Text, nullable=False),
    Column("turn_index", Integer, nullable=False),
    Column("tier_used", Text, nullable=False),
    Column("model_name", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("prompt_tokens", Integer, nullable=False),
    Column("completion_tokens", Integer, nullable=False),
    Column("latency_ms", Float, nullable=False),
    Column("cost_cents", Float, nullable=False),
    Column("tool_calls", Integer, nullable=False),
    Column("skill_used", Text),
    Column("history_compacted", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def ensure_balance(*, rls_engine: Engine, user_id: str) -> int:
    """Return the user's balance, creating the row with the default on first use."""
    with rls_engine.begin() as conn:
        row = conn.execute(
            select(_credits_t.c.balance).where(_credits_t.c.user_id == user_id)
        ).first()
        if row is not None:
            return int(row[0])
        conn.execute(insert(_credits_t).values(user_id=user_id, balance=_DEFAULT_BALANCE))
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
            update(_credits_t)
            .where(_credits_t.c.user_id == user_id)
            .values(balance=_credits_t.c.balance - amount, updated_at=text("now()"))
            .returning(_credits_t.c.balance)
        ).scalar_one()
        conn.execute(
            insert(_credit_tx_t).values(
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
            update(_credits_t)
            .where(_credits_t.c.user_id == user_id)
            .values(balance=_credits_t.c.balance + amount, updated_at=text("now()"))
            .returning(_credits_t.c.balance)
        ).scalar_one()
        conn.execute(
            insert(_credit_tx_t).values(
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
                select(_credit_tx_t)
                .where(_credit_tx_t.c.user_id == user_id)
                .order_by(_credit_tx_t.c.created_at.desc())
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
                    _turn_logs_t,
                    _conversations_t.c.persona_id.label("persona_id"),
                )
                .select_from(
                    _turn_logs_t.join(
                        _conversations_t,
                        _turn_logs_t.c.conversation_id == _conversations_t.c.id,
                    )
                )
                .order_by(_turn_logs_t.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
