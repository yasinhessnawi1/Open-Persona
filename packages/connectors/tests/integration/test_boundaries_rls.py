"""Conversation-boundary mechanics on real PG — /new + idle-timeout (Spec C1 T8).

Criterion 5: boundaries are per-persona-per-channel. ``/new`` ends only the active
persona's conversation and starts fresh (others untouched); the idle sweep ends a
persona's conversation after a gap, per-persona, and clears the channel pointer —
recent conversations are spared. Re-foregrounding an ended persona starts fresh
(reuses the ended slot, no UNIQUE collision).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from persona_api.middleware.rls_context import current_user_id
from persona_connectors.infra import PostgresConversationStateStore
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_TIMEOUT = timedelta(minutes=30)


def _store(rls: Engine, dispatch: Engine) -> PostgresConversationStateStore:
    return PostgresConversationStateStore(rls_engine=rls, dispatch_engine=dispatch)


def _slot(engine: Engine, *, channel: str, persona: str) -> tuple[str, str] | None:
    tok = current_user_id.set("user_a")
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT conversation_id, status FROM connector_conversations "
                    "WHERE owner_id='user_a' AND platform='telegram' "
                    "AND channel_key=:c AND persona_id=:p"
                ),
                {"c": channel, "p": persona},
            ).first()
    finally:
        current_user_id.reset(tok)
    return (row.conversation_id, row.status) if row is not None else None


def _active(engine: Engine, *, channel: str) -> str | None:
    tok = current_user_id.set("user_a")
    try:
        with engine.begin() as conn:
            return conn.execute(
                text(
                    "SELECT active_persona_id FROM connector_channels "
                    "WHERE owner_id='user_a' AND platform='telegram' AND channel_key=:c"
                ),
                {"c": channel},
            ).scalar()
    finally:
        current_user_id.reset(tok)


def test_new_ends_active_conversation_and_starts_fresh(
    app_engine: Engine, migrated_engine: Engine
) -> None:
    """/new replaces the active persona's conversation with a fresh one; persona stays active."""
    store = _store(app_engine, migrated_engine)
    a1 = store.foreground(owner_id="user_a", platform="telegram", channel_key="c1", persona_id="pa")

    new_conv = store.apply_new(owner_id="user_a", platform="telegram", channel_key="c1")

    assert new_conv is not None
    assert new_conv != a1.conversation_id  # a brand-new conversation
    assert _active(app_engine, channel="c1") == "pa"  # same persona stays active
    slot = _slot(app_engine, channel="c1", persona="pa")
    assert slot == (new_conv, "active")  # the slot now points at the fresh conversation


def test_new_with_no_active_persona_is_a_noop(app_engine: Engine, migrated_engine: Engine) -> None:
    """/new on a channel with no active persona has nothing to reset."""
    store = _store(app_engine, migrated_engine)
    assert store.apply_new(owner_id="user_a", platform="telegram", channel_key="cx") is None


def test_idle_sweep_ends_stale_clears_pointer_spares_recent(
    app_engine: Engine, migrated_engine: Engine
) -> None:
    """Criterion 5: the idle sweep ends only the stale slot (per-persona-per-channel),
    clears its channel pointer, and leaves recent conversations untouched."""
    store = _store(app_engine, migrated_engine)
    store.foreground(owner_id="user_a", platform="telegram", channel_key="c1", persona_id="pa")
    store.foreground(owner_id="user_a", platform="telegram", channel_key="c2", persona_id="pa")
    # Age c1's slot well past the timeout; c2 stays recent.
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE connector_conversations SET last_activity_at = now() - interval '2 hours' "
                "WHERE owner_id='user_a' AND channel_key='c1' AND persona_id='pa'"
            )
        )

    ended = store.sweep_idle_conversations(now=datetime.now(UTC), idle_after=_TIMEOUT)

    assert ended == 1  # only the stale c1 slot
    assert _slot(app_engine, channel="c1", persona="pa") == (
        _slot(app_engine, channel="c1", persona="pa")[0],
        "ended",
    )
    assert _active(app_engine, channel="c1") is None  # pointer cleared on the ended channel
    assert _slot(app_engine, channel="c2", persona="pa")[1] == "active"  # recent spared
    assert _active(app_engine, channel="c2") == "pa"


def test_foreground_after_idle_end_starts_fresh(
    app_engine: Engine, migrated_engine: Engine
) -> None:
    """Re-foregrounding an idle-ended persona starts a fresh conversation (reuses the slot)."""
    store = _store(app_engine, migrated_engine)
    a1 = store.foreground(owner_id="user_a", platform="telegram", channel_key="c1", persona_id="pa")
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE connector_conversations SET last_activity_at = now() - interval '2 hours' "
                "WHERE owner_id='user_a' AND channel_key='c1' AND persona_id='pa'"
            )
        )
    store.sweep_idle_conversations(now=datetime.now(UTC), idle_after=_TIMEOUT)
    assert _slot(app_engine, channel="c1", persona="pa")[1] == "ended"

    a2 = store.foreground(owner_id="user_a", platform="telegram", channel_key="c1", persona_id="pa")
    assert a2.resumed is False  # a fresh conversation, not a resume of the ended one
    assert a2.conversation_id != a1.conversation_id
    assert _slot(app_engine, channel="c1", persona="pa") == (a2.conversation_id, "active")
