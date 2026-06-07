"""Per-user in-flight concurrency cap for image generation (spec 15 T14).

The image generation flow caps each user at **one in-flight call** so a
runaway agentic loop cannot fire N requests in parallel and burn N
generations of real-money credit before the per-minute rate limiter
catches up. Cost discipline lives in three structural layers per the
:doc:`docs/specs/phase2/spec_15/decisions.md` gate paragraph #3:

1. Pre-deduct credits atomically before the provider call (D-15-X-pre-deduct-credits).
2. **This module — per-user in-flight cap = 1 via Postgres advisory lock.**
3. Per-minute rate limit on the route (Spec 08's existing middleware).

The lock is implemented with :func:`pg_try_advisory_xact_lock` keyed by
``hash(user_id)``. This is **multi-worker-correct from day one** — the
lock is held by the Postgres transaction, not by an in-process state
machine, so a second API worker hitting a busy user gets the same
``acquired=False`` answer the first worker would have given itself.
``D-15-X-concurrency-cap`` explicitly rejects the async-semaphore
alternative because in-process state does not survive a multi-worker
deploy (Phase 1 S08-4 is single-worker v0.1 but the cap must outlive
that without a future migration).

**Why ``pg_try_advisory_xact_lock`` and not ``pg_advisory_xact_lock``?**
The ``_try_`` variant returns immediately with ``false`` when the lock
is held, never blocking. We want the *fast 429 + Retry-After* response,
not a held HTTP connection waiting for the prior generation to commit
— which on a 30s p95 image generation would itself be a denial-of-wallet
amplifier (the client would happily fire 10 more retries against the
held connection).

**Why ``_xact_`` and not the session variant?** The transactional lock
is auto-released on commit or rollback. We rely on that to avoid leaking
locks when the surrounding ``rls_engine.begin()`` raises mid-flight; the
session-scoped variant would require an explicit ``pg_advisory_unlock``
on every exit path and a stray exception between try and release would
strand the lock for the connection's lifetime.

**Why ``md5(user_id)`` and not ``hashtext(user_id)``?** ``hashtext`` is
an internal Postgres function that has changed hash output between
versions; ``md5`` is part of the public surface and stable. The
``('x' || md5(...))::bit(64)::bigint`` chain takes the first 16 hex
chars of the digest, parses them as a bit(64), and reinterprets the bit
pattern as a signed bigint — exactly what
:func:`pg_try_advisory_xact_lock` expects. Collision risk on 64 bits of
md5 is ~2^-32 across all concurrent users; the cap remains correct on
collision (two colliding users would share a slot, the second blocks
correctly — pessimistic, not incorrect).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Connection

__all__ = ["acquire_user_concurrency"]


# The advisory-lock key derivation:
#   * ``md5(:user_id)`` → 32-hex-char digest (stable across PG versions).
#   * ``'x' || md5(...)`` → ``xHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHH`` so the
#     leading ``x`` lets ``::bit(64)`` interpret the next 16 hex chars as
#     a 64-bit bit string (Postgres parses the ``x`` prefix as hex notation).
#   * ``::bit(64)`` → 64-bit bit string of those 16 hex chars.
#   * ``::bigint`` → reinterpret the bit pattern as a signed bigint —
#     exactly the type ``pg_try_advisory_xact_lock(bigint)`` accepts.
# The cast chain is the documented Postgres-friendly way to derive a
# bigint advisory-lock key from arbitrary text; see PostgreSQL docs on
# ``pg_try_advisory_xact_lock`` and the bit-string ``x`` literal form.
_TRY_LOCK_SQL = text(
    "SELECT pg_try_advisory_xact_lock(('x' || md5(:user_id))::bit(64)::bigint) AS acquired"
)


@contextmanager
def acquire_user_concurrency(
    *,
    conn: Connection,
    user_id: str,
) -> Iterator[bool]:
    """Try to acquire a per-user advisory transactional lock; yield acquisition status.

    The lock is held for the lifetime of the *enclosing* transaction on
    ``conn`` and auto-releases on commit or rollback — callers do NOT
    call any release primitive themselves. This is exactly the property
    that makes the helper safe in the presence of mid-flight exceptions:
    if the surrounding ``rls_engine.begin()`` rolls back because the
    provider raised, the lock is released without leaking.

    Args:
        conn: An open SQLAlchemy :class:`~sqlalchemy.engine.Connection`
            already inside a transaction (typically yielded by
            ``rls_engine.begin()`` or ``rls_connection(rls_engine, user_id)``).
            Must NOT be a fresh connection in autocommit mode — the
            transactional lock variant requires an open transaction to
            scope its release.
        user_id: The opaque tenant identifier. Hashed via ``md5`` into a
            bigint for the lock key (collision space 2^-32; pessimistic
            on collision — see module docstring).

    Yields:
        ``True`` if the lock was acquired (the current transaction
        holds the slot; the caller may proceed with the protected
        operation), ``False`` if the slot is busy on another transaction
        (the caller must raise
        :class:`persona_api.errors.ConcurrencyCappedError` and surface
        429 + ``Retry-After`` to the client).

    Notes:
        The ``try`` variant never blocks; if the lock is held, the
        function returns immediately so the API responds quickly with
        429 rather than holding the HTTP connection open while a 30s
        image generation completes elsewhere.

    Example:
        >>> with rls_engine.begin() as conn:                # doctest: +SKIP
        ...     with acquire_user_concurrency(
        ...         conn=conn, user_id="user_abc"
        ...     ) as acquired:
        ...         if not acquired:
        ...             raise ConcurrencyCappedError(
        ...                 "already in flight",
        ...                 context={"user_id": "user_abc"},
        ...             )
        ...         # ... do the protected work inside the same transaction ...
    """
    row = conn.execute(_TRY_LOCK_SQL, {"user_id": user_id}).first()
    acquired = bool(row.acquired) if row is not None else False
    yield acquired
