"""current_foreground on real PG — the no-name read, cross-tenant safe (Spec C1 flow).

Like resolve_channel, this read is owner-scoped and must not leak across tenants.
Proven on real Postgres: an owner reads their channel's active persona + live
conversation; a different owner reads None for the same channel key (RLS confines);
a channel with no active persona reads None.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona_api.middleware.rls_context import current_user_id
from persona_connectors.infra import PostgresConversationStateStore

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_reads_the_active_persona_and_conversation(app_engine: Engine) -> None:
    """After foregrounding pa on a channel, current_foreground returns it + its conversation."""
    store = PostgresConversationStateStore(rls_engine=app_engine)
    token = current_user_id.set("user_a")
    try:
        fg = store.foreground(
            owner_id="user_a", platform="telegram", channel_key="cf1", persona_id="pa"
        )
        ref = store.current_foreground(owner_id="user_a", platform="telegram", channel_key="cf1")
    finally:
        current_user_id.reset(token)

    assert ref is not None
    assert ref.persona_id == "pa"
    assert ref.conversation_id == fg.conversation_id


def test_current_foreground_is_cross_tenant_safe(app_engine: Engine) -> None:
    """user_a foregrounds a persona; user_b reads None for the same channel (no leak)."""
    store = PostgresConversationStateStore(rls_engine=app_engine)

    token = current_user_id.set("user_a")
    try:
        store.foreground(owner_id="user_a", platform="telegram", channel_key="cf2", persona_id="pa")
    finally:
        current_user_id.reset(token)

    # current_foreground self-scopes to the owner_id argument; user_b cannot see
    # user_a's channel even by guessing the same channel key.
    ref = store.current_foreground(owner_id="user_b", platform="telegram", channel_key="cf2")
    assert ref is None


def test_no_active_persona_reads_none(app_engine: Engine) -> None:
    """A channel that was never foregrounded reads None (the flow then lists/auto-foregrounds)."""
    store = PostgresConversationStateStore(rls_engine=app_engine)
    ref = store.current_foreground(owner_id="user_a", platform="telegram", channel_key="never")
    assert ref is None
