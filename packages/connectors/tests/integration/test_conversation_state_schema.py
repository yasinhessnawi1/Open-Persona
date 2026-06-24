"""Connector conversation-state schema — RLS + the parallel-model invariants (C1 T5).

Schema-level proof on real PG (the state-machine LOGIC is a later task):

1. **RLS isolation** — connector_channels + connector_conversations are owner-scoped;
   user_b never sees user_a's rows.
2. **One active-persona pointer per channel** — UNIQUE(owner, platform, channel).
3. **Per-persona parallel conversations** — UNIQUE(owner, platform, channel, persona):
   two DIFFERENT personas coexist on one channel (the parallel model), a second
   row for the SAME persona is rejected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona_api.middleware.rls_context import current_user_id
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def _seed_personas_and_conversations(engine: Engine) -> None:
    """Seed two real conversations (FK targets) for user_a's session-seeded personas."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO conversations (id, owner_id, persona_id) VALUES "
                "('ca','user_a','pa'), ('ca2','user_a','pa2')"
            )
        )


def _insert_channel(
    engine: Engine, *, owner: str, platform: str, channel: str, persona: str
) -> None:
    tok = current_user_id.set(owner)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO connector_channels "
                    "(owner_id, platform, channel_key, active_persona_id) "
                    "VALUES (:o, :p, :c, :persona)"
                ),
                {"o": owner, "p": platform, "c": channel, "persona": persona},
            )
    finally:
        current_user_id.reset(tok)


def _insert_conversation(
    engine: Engine, *, owner: str, platform: str, channel: str, persona: str, conversation: str
) -> None:
    tok = current_user_id.set(owner)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO connector_conversations "
                    "(owner_id, platform, channel_key, persona_id, conversation_id) "
                    "VALUES (:o, :p, :c, :persona, :conv)"
                ),
                {"o": owner, "p": platform, "c": channel, "persona": persona, "conv": conversation},
            )
    finally:
        current_user_id.reset(tok)


def test_rls_isolates_connector_state_per_owner(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    """user_b never sees user_a's channel pointer / conversation rows (RLS)."""
    _seed_personas_and_conversations(migrated_engine)
    _insert_channel(app_engine, owner="user_a", platform="telegram", channel="c1", persona="pa")
    _insert_conversation(
        app_engine,
        owner="user_a",
        platform="telegram",
        channel="c1",
        persona="pa",
        conversation="ca",
    )

    tok = current_user_id.set("user_b")
    try:
        with app_engine.begin() as conn:
            chans = conn.execute(text("SELECT count(*) AS n FROM connector_channels")).scalar()
            convs = conn.execute(text("SELECT count(*) AS n FROM connector_conversations")).scalar()
    finally:
        current_user_id.reset(tok)
    assert chans == 0
    assert convs == 0


def test_one_active_persona_pointer_per_channel(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    """UNIQUE(owner, platform, channel): a second pointer for the same channel is rejected."""
    _seed_personas_and_conversations(migrated_engine)
    _insert_channel(app_engine, owner="user_a", platform="telegram", channel="c1", persona="pa")
    with pytest.raises(IntegrityError):
        _insert_channel(
            app_engine, owner="user_a", platform="telegram", channel="c1", persona="pa2"
        )


def test_parallel_conversations_one_per_persona_per_channel(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    """Two personas coexist on one channel (parallel model); a duplicate persona row is rejected."""
    _seed_personas_and_conversations(migrated_engine)
    # Two DIFFERENT personas on the same channel — both persist (the parallel model).
    _insert_conversation(
        app_engine,
        owner="user_a",
        platform="telegram",
        channel="c1",
        persona="pa",
        conversation="ca",
    )
    _insert_conversation(
        app_engine,
        owner="user_a",
        platform="telegram",
        channel="c1",
        persona="pa2",
        conversation="ca2",
    )
    tok = current_user_id.set("user_a")
    try:
        with app_engine.begin() as conn:
            n = conn.execute(text("SELECT count(*) AS n FROM connector_conversations")).scalar()
    finally:
        current_user_id.reset(tok)
    assert n == 2  # both personas' conversations coexist on the one channel

    # A SECOND conversation row for the SAME persona on the SAME channel is rejected.
    with pytest.raises(IntegrityError):
        _insert_conversation(
            app_engine,
            owner="user_a",
            platform="telegram",
            channel="c1",
            persona="pa",
            conversation="ca",
        )


def test_conversation_id_is_one_to_one_with_its_slot(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    """UNIQUE(conversation_id): a single conversation cannot be double-linked to two
    slots (a T6 state-machine bug guard)."""
    _seed_personas_and_conversations(migrated_engine)
    _insert_conversation(
        app_engine,
        owner="user_a",
        platform="telegram",
        channel="c1",
        persona="pa",
        conversation="ca",
    )
    # A different slot (different persona) pointing at the SAME conversation 'ca' is rejected.
    with pytest.raises(IntegrityError):
        _insert_conversation(
            app_engine,
            owner="user_a",
            platform="telegram",
            channel="c2",
            persona="pa2",
            conversation="ca",
        )
