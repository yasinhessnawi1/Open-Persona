"""Adversarial RLS-through-connector — the criteria-6/7 hard gates (Spec C1 T3).

Against real Postgres. The identity-mapping security spine, proven adversarially:

1. **Resolve isolation** — a linked identity resolves to ITS owner; an unlinked
   identity resolves to NOTHING (``IdentityNotLinkedError`` → zero access).
2. **RLS on the owner-scoped side** — under ``current_user_id = user_a``, the
   ``persona_app`` role sees ONLY user_a's ``connector_identities`` rows; user_b's
   are invisible (the structural cross-user-breach guard — RLS, not app logic).
3. **One-active-owner** — the partial-active UNIQUE forbids binding an identity
   already actively bound to another owner (an attempted hijack raises).
4. **Link → unlink → re-link** works on real PG (the partial-active allowance).

The dispatch engine (superuser/BYPASSRLS) serves the pre-auth resolve/redeem
reads; the RLS engine serves the owner-scoped writes/reads — exactly the D-C1-5
split.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from persona_api.middleware.rls_context import current_user_id
from persona_connectors.domain.linking import LinkingService
from persona_connectors.errors import IdentityNotLinkedError
from persona_connectors.infra import PostgresLinkStore
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
_TTL = timedelta(minutes=15)

# user_a / user_b are seeded + committed by the autouse ``seeded_tenants`` fixture
# before each test (FK parents present deterministically).


def _link(svc: LinkingService, *, owner: str, platform: str, identity: str) -> None:
    """Drive a full issue→redeem→bind for ``owner`` (sets the owner GUC for the
    RLS-scoped issue write, as the authenticated issue route would)."""
    token = current_user_id.set(owner)
    try:
        plaintext = svc.issue(owner_id=owner, platform=platform, now=_NOW, ttl=_TTL)
    finally:
        current_user_id.reset(token)
    svc.redeem_and_bind(
        plaintext_token=plaintext, platform=platform, platform_identity=identity, now=_NOW
    )


def test_linked_identity_resolves_to_its_owner_unlinked_is_zero_access(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    """Criterion 7: a linked identity → its owner; an unlinked identity → no access."""
    store = PostgresLinkStore(rls_engine=app_engine, dispatch_engine=migrated_engine)
    svc = LinkingService(store)

    _link(svc, owner="user_a", platform="telegram", identity="tg-a")

    assert svc.resolve_owner(platform="telegram", platform_identity="tg-a") == "user_a"
    with pytest.raises(IdentityNotLinkedError):
        svc.resolve_owner(platform="telegram", platform_identity="tg-stranger")


def test_rls_forbids_reading_another_owners_identities(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    """Criterion 7 (structural): under user_a's GUC, persona_app sees ONLY user_a's
    connector_identities — user_b's are invisible (RLS, not app logic)."""
    store = PostgresLinkStore(rls_engine=app_engine, dispatch_engine=migrated_engine)
    svc = LinkingService(store)
    _link(svc, owner="user_a", platform="telegram", identity="tg-a")
    _link(svc, owner="user_b", platform="telegram", identity="tg-b")

    token = current_user_id.set("user_a")
    try:
        with app_engine.begin() as conn:
            rows = conn.execute(text("SELECT owner_id FROM connector_identities")).all()
    finally:
        current_user_id.reset(token)

    owners = {r.owner_id for r in rows}
    assert owners == {"user_a"}  # user_b's binding is invisible under RLS


def test_partial_active_unique_blocks_cross_owner_hijack(
    migrated_engine: Engine, app_engine: Engine
) -> None:
    """Criterion 7: an identity already ACTIVE for user_a cannot be bound to user_b
    (the partial-active UNIQUE is the cross-user-breach guard)."""
    store = PostgresLinkStore(rls_engine=app_engine, dispatch_engine=migrated_engine)
    svc = LinkingService(store)
    _link(svc, owner="user_a", platform="telegram", identity="tg-shared")

    with pytest.raises(IntegrityError):
        store.bind_identity(
            platform="telegram", platform_identity="tg-shared", owner_id="user_b", now=_NOW
        )


def test_link_unlink_relink_on_real_postgres(migrated_engine: Engine, app_engine: Engine) -> None:
    """Criterion 6: unlink severs access; a fresh token re-links (partial-active allowance)."""
    store = PostgresLinkStore(rls_engine=app_engine, dispatch_engine=migrated_engine)
    svc = LinkingService(store)

    _link(svc, owner="user_a", platform="telegram", identity="tg-a")
    svc.unlink(owner_id="user_a", platform="telegram", platform_identity="tg-a", now=_NOW)
    with pytest.raises(IdentityNotLinkedError):
        svc.resolve_owner(platform="telegram", platform_identity="tg-a")

    _link(svc, owner="user_a", platform="telegram", identity="tg-a")
    assert svc.resolve_owner(platform="telegram", platform_identity="tg-a") == "user_a"
