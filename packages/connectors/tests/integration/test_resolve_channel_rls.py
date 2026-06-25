"""resolve_channel on real PG — the GAP-A reverse lookup, cross-tenant safe (Spec C2 T5).

The load-bearing property of the GAP-A correction (D-C2-X-gap-a-resolve-channel): a
reverse lookup ``conversation_id → (platform, channel_key)`` that could leak across
tenants would be WORSE than the original gap. So this proves, on real Postgres:

1. an owner resolves their OWN conversation's channel;
2. a DIFFERENT owner's scope over the same ``conversation_id`` returns ``None``
   (RLS confines the read — no cross-tenant leak), even though the row exists;
3. no scope / an unknown id → ``None`` (fail-closed).

Owner-scoping is the caller's responsibility (set via the ``current_user_id``
GUC) — exactly the deliver path's ``owner_scope`` → ``resolve_channel`` step.
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

# A conversation owned by user_a, on persona pa, reachable on telegram channel "cA".
_CONV = "conv_resolve_a"


def _seed_user_a_channel(engine: Engine) -> None:
    """Seed (superuser/BYPASSRLS) user_a's conversation + its connector channel row."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO conversations (id, owner_id, persona_id) VALUES (:c, 'user_a', 'pa')"
            ),
            {"c": _CONV},
        )
        conn.execute(
            text(
                "INSERT INTO connector_conversations "
                "(owner_id, platform, channel_key, persona_id, conversation_id, status) "
                "VALUES ('user_a', 'telegram', 'cA', 'pa', :c, 'active')"
            ),
            {"c": _CONV},
        )


def test_owner_resolves_their_own_channel(app_engine: Engine, migrated_engine: Engine) -> None:
    """Under user_a's scope, resolve_channel returns the right (platform, channel_key)."""
    _seed_user_a_channel(migrated_engine)
    store = PostgresConversationStateStore(rls_engine=app_engine)

    token = current_user_id.set("user_a")
    try:
        ref = store.resolve_channel(conversation_id=_CONV)
    finally:
        current_user_id.reset(token)

    assert ref is not None
    assert ref.platform == "telegram"
    assert ref.channel_key == "cA"


def test_resolve_channel_is_cross_tenant_safe(app_engine: Engine, migrated_engine: Engine) -> None:
    """Under user_b's scope, user_a's conversation_id resolves to None — NO leak (criterion 9)."""
    _seed_user_a_channel(migrated_engine)
    store = PostgresConversationStateStore(rls_engine=app_engine)

    token = current_user_id.set("user_b")
    try:
        ref = store.resolve_channel(conversation_id=_CONV)
    finally:
        current_user_id.reset(token)

    assert ref is None  # the row exists, but RLS confines the read to its owner


def test_unknown_conversation_resolves_to_none(app_engine: Engine) -> None:
    """An unknown conversation_id → None (fail-closed)."""
    store = PostgresConversationStateStore(rls_engine=app_engine)
    token = current_user_id.set("user_a")
    try:
        ref = store.resolve_channel(conversation_id="conv_does_not_exist")
    finally:
        current_user_id.reset(token)
    assert ref is None
