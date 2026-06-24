"""Inbound resolution → owner-scope → downstream RLS, end-to-end on real PG (C1 T4).

The criterion-7 chain proven through the connector: an inbound platform identity
resolves (pre-auth, cross-tenant via the dispatch engine) to its linked owner;
scoping ``current_user_id`` to that owner then confines every downstream read to
that owner's rows (RLS) — so the persona-on-a-connector sees exactly what the
owner sees on the web, and never another user's personas. An unlinked identity
resolves to a link-instruction with no owner — zero access.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from persona_api.middleware.rls_context import current_user_id
from persona_connectors.domain.linking import LinkingService
from persona_connectors.domain.normalise import NormalisedInbound
from persona_connectors.domain.resolution import (
    InboundIdentityResolver,
    ResolvedIdentity,
    UnlinkedIdentity,
)
from persona_connectors.infra import PostgresLinkStore
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
_TTL = timedelta(minutes=15)


def _inbound(platform: str, sender_id: str) -> NormalisedInbound:
    return NormalisedInbound(
        platform=platform,
        sender_id=sender_id,
        conversation_key=sender_id,
        message_id="m-1",
        text="hei",
        received_at=_NOW,
    )


def _link(svc: LinkingService, *, owner: str, platform: str, identity: str) -> None:
    token = current_user_id.set(owner)
    try:
        plaintext = svc.issue(owner_id=owner, platform=platform, now=_NOW, ttl=_TTL)
    finally:
        current_user_id.reset(token)
    svc.redeem_and_bind(
        plaintext_token=plaintext, platform=platform, platform_identity=identity, now=_NOW
    )


def test_resolution_then_owner_scope_confines_downstream_to_the_owner(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    """Resolve user_a's identity → scope to user_a → a downstream persona read sees
    ONLY user_a's persona (pa), never user_b's (pb). The full criterion-7 wire."""
    svc = LinkingService(PostgresLinkStore(rls_engine=app_engine, dispatch_engine=migrated_engine))
    _link(svc, owner="user_a", platform="telegram", identity="tg-a")
    resolver = InboundIdentityResolver(svc)

    result = resolver.resolve(_inbound("telegram", "tg-a"))
    assert isinstance(result, ResolvedIdentity)
    assert result.owner_id == "user_a"

    # Downstream runs scoped to the resolved owner — RLS confines the read.
    token = current_user_id.set(result.owner_id)
    try:
        with app_engine.begin() as conn:
            personas = [r.id for r in conn.execute(text("SELECT id FROM personas")).all()]
    finally:
        current_user_id.reset(token)

    # user_a sees their own personas (pa/pa2); user_b's pb is invisible under RLS.
    assert "pa" in personas
    assert "pb" not in personas


def test_unlinked_inbound_resolves_to_instruction_zero_access(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    """An unlinked inbound yields a link-instruction and no owner — there is no
    scope to enter, so no persona is ever reachable."""
    svc = LinkingService(PostgresLinkStore(rls_engine=app_engine, dispatch_engine=migrated_engine))
    _link(svc, owner="user_a", platform="telegram", identity="tg-a")
    resolver = InboundIdentityResolver(svc)

    result = resolver.resolve(_inbound("telegram", "an-unlinked-stranger"))
    assert isinstance(result, UnlinkedIdentity)
    assert "telegram" in result.instruction.lower()
