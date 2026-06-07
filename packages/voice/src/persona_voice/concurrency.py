"""Per-user in-flight concurrency cap for voice calls (spec V1 T09).

Persona-voice caps each user at **one in-flight voice call** so a runaway
client cannot fire N concurrent room-join attempts and burn through TURN
bandwidth, LiveKit agent-worker slots, or open RLS engines before the
per-minute rate limiter catches up. This mirrors
:doc:`/docs/specs/phase2/spec_15/decisions.md` D-15-X-concurrency-cap
verbatim per D-V1-5 + D-V1-X-d15x-precedent-binding (the kickoff's
"Postgres rate-limit table" generic lean was wrong; the advisory-lock
shape is the corrected precedent).

The lock is :func:`pg_try_advisory_xact_lock` keyed by ``hash(user_id)``.
**Multi-worker-correct from day one** — the lock is held by the Postgres
transaction, not by an in-process state machine, so a second persona-voice
agent worker hitting a busy user gets the same ``acquired=False`` answer
the first worker would have given itself. S08-4 single-worker assumption
is broken by voice (LiveKit-Agents-style worker pools and the broader
multi-worker move post-Phase-1); the advisory-lock approach survives that
migration without code change.

**Why ``pg_try_advisory_xact_lock`` and not the blocking variant?** The
``_try_`` variant returns immediately with ``false`` when the lock is
held, never blocking. We want the *fast 429 + Retry-After* response on the
``POST /v1/voice/token`` endpoint, not a held HTTP connection waiting for
the prior call to end — which on a 30-minute voice call would itself be a
denial-of-wallet amplifier (the client would happily fire 10 more retries
against the held connection).

**Why ``_xact_`` and not the session variant?** The transactional lock is
auto-released on commit or rollback. We rely on that to avoid leaking
locks when the surrounding ``rls_engine.begin()`` raises mid-flight; the
session-scoped variant would require an explicit
``pg_advisory_unlock`` on every exit path and a stray exception between
try and release would strand the lock for the connection's lifetime —
the exact failure mode the LiveKit agent-worker reconnection path
(T12) needs to NOT have.

**Why ``md5(user_id)`` and not ``hashtext(user_id)``?** ``hashtext`` is
an internal Postgres function that has changed hash output between
versions; ``md5`` is part of the public surface and stable. The
``('x' || md5(...))::bit(64)::bigint`` chain takes the first 16 hex chars
of the digest, parses them as a bit(64), and reinterprets the bit pattern
as a signed bigint — exactly what :func:`pg_try_advisory_xact_lock`
expects. Collision risk on 64 bits of md5 is ~2^-32 across all concurrent
users; the cap remains correct on collision (two colliding users would
share a slot, the second blocks correctly — pessimistic, not incorrect).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from persona.errors import PersonaError
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Connection

__all__ = [
    "VoiceConcurrencyCappedError",
    "acquire_voice_call_concurrency",
]


class VoiceConcurrencyCappedError(PersonaError):
    """Raised when a user already has a voice call in flight.

    The persona-voice HTTP endpoint translates this to a 429 +
    ``Retry-After`` header, mirroring persona-api's :class:`ConcurrencyCappedError`
    (D-V1-X-d15x-precedent-binding).
    """


# The advisory-lock key derivation mirrors persona-api's image-gen path verbatim
# (D-15-X-concurrency-cap): ``'x' || md5(:user_id)`` produces a ``x...`` literal
# that ``::bit(64)`` parses as a 64-bit bit string of the first 16 hex chars;
# ``::bigint`` reinterprets the bit pattern as a signed bigint —
# :func:`pg_try_advisory_xact_lock`'s parameter type. The cast chain is the
# documented Postgres-friendly way to derive a bigint advisory-lock key from
# arbitrary text.
_TRY_LOCK_SQL = text(
    "SELECT pg_try_advisory_xact_lock(('x' || md5(:user_id))::bit(64)::bigint) AS acquired"
)


@contextmanager
def acquire_voice_call_concurrency(
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
    LiveKit token issuance raised, the lock is released without leaking.

    Args:
        conn: An open SQLAlchemy :class:`~sqlalchemy.engine.Connection`
            already inside a transaction (typically yielded by
            ``rls_engine.begin()`` on the persona-voice session-bound
            engine T06 provides). Must NOT be a fresh connection in
            autocommit mode — the transactional lock variant requires an
            open transaction to scope its release.
        user_id: The opaque tenant identifier (Spec 08 JWT ``sub`` claim).
            Hashed via ``md5`` into a bigint for the lock key (collision
            space 2^-32; pessimistic on collision — see module docstring).

    Yields:
        ``True`` if the lock was acquired (the current transaction holds
        the slot; the caller may proceed with issuing the LiveKit token
        and joining the Room), ``False`` if the slot is busy on another
        transaction (the caller must raise
        :class:`VoiceConcurrencyCappedError` and surface 429 +
        ``Retry-After`` to the client).

    Notes:
        The ``try`` variant never blocks; if the lock is held, the
        function returns immediately so the API responds quickly with
        429 rather than holding the HTTP connection open while a
        30-minute voice call completes elsewhere.

    Example:
        >>> with rls_engine.begin() as conn:                # doctest: +SKIP
        ...     with acquire_voice_call_concurrency(
        ...         conn=conn, user_id="user_abc"
        ...     ) as acquired:
        ...         if not acquired:
        ...             raise VoiceConcurrencyCappedError(
        ...                 "already in flight",
        ...                 context={"user_id": "user_abc"},
        ...             )
        ...         # ... issue LiveKit token inside the same transaction ...
    """
    row = conn.execute(_TRY_LOCK_SQL, {"user_id": user_id}).first()
    acquired = bool(row.acquired) if row is not None else False
    yield acquired
