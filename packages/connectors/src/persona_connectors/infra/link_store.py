"""The concrete ``LinkStore`` over persona-api's connector tables (Spec C1 T3).

Implements the owned-surface :class:`~persona_connectors.domain.linking.LinkStore`
port (the C0 recorder pattern). **API-coupled** — it imports persona-api's
``connector_link_tokens`` / ``connector_identities`` tables + the
``current_user_id`` RLS contextvar; it lives in ``infra`` (allow-listed by the
decoupling guard) so the ``domain`` owned surface stays api-free.

Engine split (D-C1-5, the A0-worker pre-auth pattern):

- **owner-scoped writes** — :meth:`create_token` (issue) and :meth:`revoke_identity`
  (unlink) run on the **RLS engine** under ``current_user_id = owner``, so RLS
  WITH CHECK confirms the row's owner;
- **pre-auth cross-tenant ops** — :meth:`get_token_by_hash` / :meth:`get_active_identity`
  (reads keyed by the unguessable hash / the UNIQUE-active spine) and the redeem
  writes :meth:`consume_token` / :meth:`bind_identity` run on the **dispatch
  (BYPASSRLS) engine**. The redeem writes are gated by the service's prior token
  validation + the partial-active UNIQUE constraint (the cross-user-breach guard
  is the constraint, not RLS) — the inbound redeemer is not yet an authenticated
  owner, so there is no contextvar to scope by.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from persona_api.db.models import connector_identities, connector_link_tokens
from persona_api.middleware.rls_context import current_user_id
from sqlalchemy import insert, select, update

from persona_connectors.domain.linking import LinkRecord, LinkToken

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    from sqlalchemy.engine import Engine

__all__ = ["PostgresLinkStore"]


class PostgresLinkStore:
    """A :class:`LinkStore` backed by the connector tables on Postgres."""

    def __init__(self, *, rls_engine: Engine, dispatch_engine: Engine) -> None:
        self._rls = rls_engine
        self._dispatch = dispatch_engine

    @contextlib.contextmanager
    def _owner_scope(self, owner_id: str) -> Iterator[None]:
        """Scope an owner-confined write to ``owner_id`` (RLS WITH CHECK)."""
        token = current_user_id.set(owner_id)
        try:
            yield
        finally:
            current_user_id.reset(token)

    # --- owner-scoped writes (RLS engine) ---

    def create_token(self, token: LinkToken) -> None:
        with self._owner_scope(token.owner_id), self._rls.begin() as conn:
            conn.execute(
                insert(connector_link_tokens).values(
                    token_hash=token.token_hash,
                    owner_id=token.owner_id,
                    platform=token.platform,
                    status=token.status,
                    expires_at=token.expires_at,
                    created_at=token.created_at,
                    consumed_at=token.consumed_at,
                )
            )

    def revoke_identity(
        self, *, owner_id: str, platform: str, platform_identity: str, now: datetime
    ) -> None:
        with self._owner_scope(owner_id), self._rls.begin() as conn:
            conn.execute(
                update(connector_identities)
                .where(
                    connector_identities.c.owner_id == owner_id,
                    connector_identities.c.platform == platform,
                    connector_identities.c.platform_identity == platform_identity,
                    connector_identities.c.status == "active",
                )
                .values(status="revoked", revoked_at=now)
            )

    # --- pre-auth cross-tenant ops (dispatch engine, BYPASSRLS) ---

    def get_token_by_hash(self, token_hash: str) -> LinkToken | None:
        with self._dispatch.begin() as conn:
            row = (
                conn.execute(
                    select(
                        connector_link_tokens.c.token_hash,
                        connector_link_tokens.c.owner_id,
                        connector_link_tokens.c.platform,
                        connector_link_tokens.c.status,
                        connector_link_tokens.c.expires_at,
                        connector_link_tokens.c.created_at,
                        connector_link_tokens.c.consumed_at,
                    ).where(connector_link_tokens.c.token_hash == token_hash)
                )
                .mappings()
                .first()
            )
        return LinkToken(**row) if row is not None else None

    def consume_token(self, token_hash: str, *, now: datetime) -> None:
        with self._dispatch.begin() as conn:
            conn.execute(
                update(connector_link_tokens)
                .where(connector_link_tokens.c.token_hash == token_hash)
                .values(status="consumed", consumed_at=now)
            )

    def bind_identity(
        self, *, platform: str, platform_identity: str, owner_id: str, now: datetime
    ) -> None:
        with self._dispatch.begin() as conn:
            conn.execute(
                insert(connector_identities).values(
                    platform=platform,
                    platform_identity=platform_identity,
                    owner_id=owner_id,
                    status="active",
                    linked_at=now,
                )
            )

    def get_active_identity(self, *, platform: str, platform_identity: str) -> LinkRecord | None:
        with self._dispatch.begin() as conn:
            row = (
                conn.execute(
                    select(
                        connector_identities.c.platform,
                        connector_identities.c.platform_identity,
                        connector_identities.c.owner_id,
                        connector_identities.c.status,
                        connector_identities.c.linked_at,
                        connector_identities.c.revoked_at,
                    ).where(
                        connector_identities.c.platform == platform,
                        connector_identities.c.platform_identity == platform_identity,
                        connector_identities.c.status == "active",
                    )
                )
                .mappings()
                .first()
            )
        return LinkRecord(**row) if row is not None else None
