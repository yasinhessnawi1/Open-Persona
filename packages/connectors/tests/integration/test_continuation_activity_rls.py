"""foreground bumps last_activity_at on continuation (Spec C1 flow — the idle-sweep fix).

The production bug this guards: a user chatting continuously with a single persona
never triggers a switch, so a stale ``last_activity_at`` would let the idle sweep end
their conversation mid-active-use. Every foreground call is an inbound message =
activity, so the no-op (continuation) path must refresh the timer — proven here:
a second foreground of the already-active persona advances ``last_activity_at`` AND
keeps the conversation intact, so the idle sweep spares an actively-used chat.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from persona_api.middleware.rls_context import current_user_id
from persona_connectors.infra import PostgresConversationStateStore
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def _last_activity(engine: Engine, *, persona: str) -> datetime:
    token = current_user_id.set("user_a")
    try:
        with engine.begin() as conn:
            value = conn.execute(
                text(
                    "SELECT last_activity_at FROM connector_conversations "
                    "WHERE owner_id='user_a' AND platform='telegram' AND channel_key='ca' "
                    "AND persona_id=:p"
                ),
                {"p": persona},
            ).scalar_one()
    finally:
        current_user_id.reset(token)
    assert isinstance(value, datetime)
    return value


def test_continuation_refreshes_last_activity_and_keeps_conversation(app_engine: Engine) -> None:
    """A second foreground of the active persona bumps last_activity_at, same conversation."""
    store = PostgresConversationStateStore(rls_engine=app_engine)
    token = current_user_id.set("user_a")
    try:
        first = store.foreground(
            owner_id="user_a", platform="telegram", channel_key="ca", persona_id="pa"
        )
        before = _last_activity(app_engine, persona="pa")
        time.sleep(0.05)  # ensure the next transaction's now() is strictly later
        second = store.foreground(
            owner_id="user_a", platform="telegram", channel_key="ca", persona_id="pa"
        )
        after = _last_activity(app_engine, persona="pa")
    finally:
        current_user_id.reset(token)

    # The continuation is a no-op on the conversation (same id, never reset)...
    assert second.conversation_id == first.conversation_id
    # ...but it DID refresh the idle timer, so an actively-used chat is never swept.
    assert after > before


def test_actively_used_conversation_survives_the_idle_sweep(
    app_engine: Engine, migrated_engine: Engine
) -> None:
    """End-to-end: continuation keeps a chat out of the idle sweep's reach."""
    store = PostgresConversationStateStore(rls_engine=app_engine, dispatch_engine=migrated_engine)
    token = current_user_id.set("user_a")
    try:
        store.foreground(owner_id="user_a", platform="telegram", channel_key="ca", persona_id="pa")
        time.sleep(0.05)
        store.foreground(  # continuation = activity, refreshes the timer
            owner_id="user_a", platform="telegram", channel_key="ca", persona_id="pa"
        )
        status = _conversation_status(app_engine, persona="pa")
    finally:
        current_user_id.reset(token)
    assert status == "active"

    # A sweep with a 1-minute idle window must NOT end the just-active conversation.
    ended = store.sweep_idle_conversations(now=datetime.now(UTC), idle_after=timedelta(minutes=1))
    assert ended == 0
    assert _conversation_status(app_engine, persona="pa") == "active"


def _conversation_status(engine: Engine, *, persona: str) -> str:
    token = current_user_id.set("user_a")
    try:
        with engine.begin() as conn:
            value = conn.execute(
                text(
                    "SELECT status FROM connector_conversations "
                    "WHERE owner_id='user_a' AND platform='telegram' AND channel_key='ca' "
                    "AND persona_id=:p"
                ),
                {"p": persona},
            ).scalar_one()
    finally:
        current_user_id.reset(token)
    assert isinstance(value, str)
    return value
