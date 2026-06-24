"""The parallel-conversation flip on real PG — criterion 3 (Spec C1 T6).

The agentic-future linchpin, proven end-to-end against the T5 schema: foreground
A → start; foreground B → A suspended (not ended), B active; foreground A again →
B suspended, A's conversation RESUMED INTACT (same conversation_id, not reset);
re-naming the active persona is a no-op. All under the owner's RLS scope, the
flip serialised by ``SELECT … FOR UPDATE`` on the channel pointer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona_api.middleware.rls_context import current_user_id
from persona_connectors.infra import PostgresConversationStateStore
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

# user_a's personas pa + pa2 are stable session seeds (conftest); A/B switching
# uses both.


def _slot_status(engine: Engine, *, persona: str) -> str | None:
    tok = current_user_id.set("user_a")
    try:
        with engine.begin() as conn:
            return conn.execute(
                text(
                    "SELECT status FROM connector_conversations "
                    "WHERE owner_id='user_a' AND platform='telegram' AND channel_key='c1' "
                    "AND persona_id=:p"
                ),
                {"p": persona},
            ).scalar()
    finally:
        current_user_id.reset(tok)


def _active_persona(engine: Engine) -> str | None:
    tok = current_user_id.set("user_a")
    try:
        with engine.begin() as conn:
            return conn.execute(
                text(
                    "SELECT active_persona_id FROM connector_channels "
                    "WHERE owner_id='user_a' AND platform='telegram' AND channel_key='c1'"
                )
            ).scalar()
    finally:
        current_user_id.reset(tok)


def test_switch_suspends_then_resumes_intact_never_resets(app_engine: Engine) -> None:
    """A → B → A: A's conversation is suspended on the switch and resumed intact (criterion 3)."""
    store = PostgresConversationStateStore(rls_engine=app_engine)

    # Foreground A (pa) — fresh start.
    a1 = store.foreground(owner_id="user_a", platform="telegram", channel_key="c1", persona_id="pa")
    assert a1.resumed is False
    assert _active_persona(app_engine) == "pa"
    assert _slot_status(app_engine, persona="pa") == "active"

    # Foreground B (pa2) — A is SUSPENDED (not ended), B becomes active.
    b1 = store.foreground(
        owner_id="user_a", platform="telegram", channel_key="c1", persona_id="pa2"
    )
    assert b1.resumed is False
    assert b1.conversation_id != a1.conversation_id  # B has its own conversation
    assert _active_persona(app_engine) == "pa2"
    assert _slot_status(app_engine, persona="pa") == "suspended"  # A suspended, NOT ended
    assert _slot_status(app_engine, persona="pa2") == "active"

    # Foreground A again — B suspended, A RESUMED with the SAME conversation (intact).
    a2 = store.foreground(owner_id="user_a", platform="telegram", channel_key="c1", persona_id="pa")
    assert a2.resumed is True
    assert a2.conversation_id == a1.conversation_id  # A's conversation continues where it left off
    assert _active_persona(app_engine) == "pa"
    assert _slot_status(app_engine, persona="pa") == "active"
    assert _slot_status(app_engine, persona="pa2") == "suspended"


def test_renaming_the_active_persona_is_a_noop(app_engine: Engine) -> None:
    """Re-naming the active persona returns its conversation unchanged — never resets."""
    store = PostgresConversationStateStore(rls_engine=app_engine)

    first = store.foreground(
        owner_id="user_a", platform="telegram", channel_key="c1", persona_id="pa"
    )
    again = store.foreground(
        owner_id="user_a", platform="telegram", channel_key="c1", persona_id="pa"
    )
    assert again.conversation_id == first.conversation_id  # same conversation — no reset
    assert _active_persona(app_engine) == "pa"
    assert _slot_status(app_engine, persona="pa") == "active"

    # Exactly one conversation row for pa (the no-op didn't create a second).
    tok = current_user_id.set("user_a")
    try:
        with app_engine.begin() as conn:
            n = conn.execute(
                text(
                    "SELECT count(*) FROM connector_conversations "
                    "WHERE owner_id='user_a' AND persona_id='pa'"
                )
            ).scalar()
    finally:
        current_user_id.reset(tok)
    assert n == 1
