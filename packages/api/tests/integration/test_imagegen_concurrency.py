"""Integration tests for the per-user advisory-lock concurrency cap (spec 15 T14).

These tests assert the structural correctness of
:func:`persona_api.imagegen.concurrency.acquire_user_concurrency` against
a real Postgres 16 (the lock primitive is a Postgres feature; mocking it
would invalidate the very property under test). The four scenarios mirror
``tasks.md`` T14's acceptance bullets:

1. Single-call cap holds and re-acquires after commit.
2. Two concurrent transactions on the same user: first acquires, second
   yields ``False`` (the binary 429-trigger condition).
3. Auto-release on rollback (no leaked lock after the surrounding
   transaction errors out).
4. Cross-user isolation — concurrent transactions for different users
   each acquire their own slot.

Each test uses the ``migrated_engine`` fixture so RLS policies are present
(D-15-X-concurrency-cap composes inside the same transaction that owns
the per-request RLS scope; the test mirrors the production wiring).
"""

# ruff: noqa: ANN401, ARG001
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona_api.imagegen.concurrency import acquire_user_concurrency
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Engine

pytestmark = pytest.mark.integration


def test_single_call_acquires_lock(migrated_engine: Engine) -> None:
    """A lone transaction always acquires the lock; the yielded bool is True."""
    with (
        migrated_engine.begin() as conn,
        acquire_user_concurrency(conn=conn, user_id="user_solo") as acquired,
    ):
        assert acquired is True


def test_lock_releases_on_commit_and_reacquires(migrated_engine: Engine) -> None:
    """Sequential transactions on the same user both acquire (commit released the prior lock)."""
    # First transaction acquires and commits.
    with (
        migrated_engine.begin() as conn,
        acquire_user_concurrency(conn=conn, user_id="user_serial") as acquired,
    ):
        assert acquired is True
    # Second transaction (after the first committed) acquires the same slot.
    with (
        migrated_engine.begin() as conn,
        acquire_user_concurrency(conn=conn, user_id="user_serial") as acquired,
    ):
        assert acquired is True


def test_lock_releases_on_rollback_and_reacquires(migrated_engine: Engine) -> None:
    """A rolled-back transaction releases the lock too — the slot reopens on the next attempt.

    This is the load-bearing property that lets the
    ``image_service.generate`` flow stay correct in the presence of
    mid-flight backend exceptions (the surrounding ``rls_engine.begin()``
    rolls back, freeing the slot without leaking).
    """

    class _BoomError(RuntimeError):
        """Synthetic error to force the surrounding transaction to roll back."""

    with pytest.raises(_BoomError):  # noqa: PT012, SIM117 — context manager body needs >1 statement
        with (
            migrated_engine.begin() as conn,
            acquire_user_concurrency(conn=conn, user_id="user_rollback") as acquired,
        ):
            assert acquired is True
            raise _BoomError("force rollback")

    # The slot must be reusable after rollback.
    with (
        migrated_engine.begin() as conn,
        acquire_user_concurrency(conn=conn, user_id="user_rollback") as acquired,
    ):
        assert acquired is True


def test_concurrent_same_user_second_call_capped(migrated_engine: Engine) -> None:
    """Two overlapping transactions for the same user — the second yields ``False``.

    This is the binary structural test of the concurrency cap. We hold
    one transaction open (lock acquired) and then open a *second*
    transaction on a *different connection* (the same SQLAlchemy
    ``Engine`` pool yields a distinct underlying psycopg connection) —
    the second transaction must observe ``acquired=False`` because the
    advisory lock for ``hash(user_id)`` is held by transaction #1.
    """
    user_id = "user_contend"
    with migrated_engine.connect() as first_conn:
        first_trans = first_conn.begin()
        try:
            row = first_conn.execute(
                text(
                    "SELECT pg_try_advisory_xact_lock("
                    "('x' || md5(:uid))::bit(64)::bigint) AS acquired"
                ),
                {"uid": user_id},
            ).first()
            assert row is not None
            assert bool(row.acquired) is True

            # While transaction #1 holds the lock, transaction #2 on a
            # separate connection must see ``acquired=False``.
            with (
                migrated_engine.connect() as second_conn,
                second_conn.begin(),
                acquire_user_concurrency(conn=second_conn, user_id=user_id) as second_acquired,
            ):
                assert second_acquired is False
        finally:
            first_trans.rollback()

    # After transaction #1 commits/rolls back, the lock releases and is
    # reusable — proof the "False" was due to contention, not a permanent
    # block.
    with (
        migrated_engine.begin() as conn,
        acquire_user_concurrency(conn=conn, user_id=user_id) as acquired,
    ):
        assert acquired is True


def test_concurrent_different_users_both_acquire(migrated_engine: Engine) -> None:
    """Two overlapping transactions for different users both acquire their slots.

    Cross-user calls must not block each other — the advisory-lock key
    is per-``user_id`` so user A's in-flight generation cannot delay
    user B's. This is the structural symmetry test for
    D-15-X-concurrency-cap.
    """
    with migrated_engine.connect() as conn_a:
        trans_a = conn_a.begin()
        try:
            with acquire_user_concurrency(conn=conn_a, user_id="user_one") as acquired_a:
                assert acquired_a is True
                # While user_one's lock is held, user_two must still
                # acquire on a separate connection.
                with (
                    migrated_engine.connect() as conn_b,
                    conn_b.begin(),
                    acquire_user_concurrency(conn=conn_b, user_id="user_two") as acquired_b,
                ):
                    assert acquired_b is True
        finally:
            trans_a.rollback()


def test_returned_value_is_bool_not_truthy(migrated_engine: Engine) -> None:
    """The yielded value is a strict ``bool`` — callers can ``is True`` / ``is False`` it.

    The helper coerces the row's ``acquired`` column with ``bool(...)``
    so callers do not depend on the underlying driver's boolean
    representation (psycopg returns Python ``bool`` for ``boolean``
    columns but the cast is defensive and the contract is "strict
    bool"). Mismatched types would silently break ``is False`` checks
    in the route layer.
    """
    with (
        migrated_engine.begin() as conn,
        acquire_user_concurrency(conn=conn, user_id="user_strict_bool") as acquired,
    ):
        assert isinstance(acquired, bool)
        assert acquired is True
