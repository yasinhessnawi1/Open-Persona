"""The concrete parallel-conversation store (Spec C1 T6) — the transactional flip.

Implements the owned-surface
:class:`~persona_connectors.domain.conversation_model.ConversationStateStore` port
over the T5 tables (``connector_channels`` + ``connector_conversations``) and the
``conversations`` table. **API-coupled** (imports persona-api's tables + the RLS
contextvar) → lives in ``infra``.

:meth:`foreground` is the atomic ``SELECT … FOR UPDATE`` flip (D-C1-2): in one
owner-scoped transaction it materialises + locks the channel pointer row
(serialising concurrent flips on that channel), reads the current active persona +
the named persona's slot, applies the pure
:func:`~persona_connectors.domain.conversation_model.decide_foreground` rules, then
persists the suspend (never end) + resume-or-start + the pointer flip. The owner is
already resolved (T4) so everything runs on the RLS engine under the owner scope —
no dispatch engine here. Postgres-targeted (``FOR UPDATE`` + ``ON CONFLICT``).
"""

from __future__ import annotations

import contextlib
import uuid
from typing import TYPE_CHECKING

from persona_api.db.models import connector_channels, connector_conversations
from persona_api.db.models import conversations as conversations_t
from persona_api.middleware.rls_context import current_user_id
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from persona_connectors.domain.conversation_model import (
    ForegroundResult,
    NoOp,
    decide_foreground,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime, timedelta

    from sqlalchemy.engine import Engine

__all__ = ["PostgresConversationStateStore"]

_RESUMABLE = ("active", "suspended")


class PostgresConversationStateStore:
    """A :class:`ConversationStateStore` backed by the connector + conversation tables."""

    def __init__(self, *, rls_engine: Engine, dispatch_engine: Engine | None = None) -> None:
        self._rls = rls_engine
        # The cross-tenant (BYPASSRLS) engine the idle sweep runs on (A0-worker
        # pattern). Required only for :meth:`sweep_idle_conversations`; the
        # owner-scoped ops (foreground / apply_new) use the RLS engine.
        self._dispatch = dispatch_engine

    @contextlib.contextmanager
    def _owner_scope(self, owner_id: str) -> Iterator[None]:
        token = current_user_id.set(owner_id)
        try:
            yield
        finally:
            current_user_id.reset(token)

    def foreground(
        self, *, owner_id: str, platform: str, channel_key: str, persona_id: str
    ) -> ForegroundResult:
        with self._owner_scope(owner_id), self._rls.begin() as conn:
            # Materialise the pointer row so the FOR UPDATE lock always has a target,
            # then lock it — concurrent flips on this channel serialise here (D-C1-2).
            conn.execute(
                pg_insert(connector_channels)
                .values(owner_id=owner_id, platform=platform, channel_key=channel_key)
                .on_conflict_do_nothing(index_elements=["owner_id", "platform", "channel_key"])
            )
            active = conn.execute(
                select(connector_channels.c.active_persona_id)
                .where(
                    connector_channels.c.owner_id == owner_id,
                    connector_channels.c.platform == platform,
                    connector_channels.c.channel_key == channel_key,
                )
                .with_for_update()
            ).scalar()

            slot = conn.execute(
                select(
                    connector_conversations.c.conversation_id,
                    connector_conversations.c.status,
                ).where(
                    connector_conversations.c.owner_id == owner_id,
                    connector_conversations.c.platform == platform,
                    connector_conversations.c.channel_key == channel_key,
                    connector_conversations.c.persona_id == persona_id,
                )
            ).first()
            named_has_resumable = slot is not None and slot.status in _RESUMABLE

            plan = decide_foreground(
                active_persona_id=active,
                named_persona_id=persona_id,
                named_has_resumable_slot=named_has_resumable,
            )
            if isinstance(plan, NoOp):
                # Re-naming the active persona: its slot IS the active one — continue it.
                assert slot is not None  # invariant: active persona always has an active slot
                return ForegroundResult(conversation_id=slot.conversation_id, resumed=True)

            # A switch: suspend the previously-active persona's conversation (never end it).
            if plan.suspend_persona_id is not None:
                conn.execute(
                    update(connector_conversations)
                    .where(
                        connector_conversations.c.owner_id == owner_id,
                        connector_conversations.c.platform == platform,
                        connector_conversations.c.channel_key == channel_key,
                        connector_conversations.c.persona_id == plan.suspend_persona_id,
                        connector_conversations.c.status == "active",
                    )
                    .values(status="suspended", updated_at=func.now())
                )

            if plan.resume:
                # Resume the named persona's suspended slot — its conversation continues intact.
                assert slot is not None
                conn.execute(
                    update(connector_conversations)
                    .where(
                        connector_conversations.c.owner_id == owner_id,
                        connector_conversations.c.platform == platform,
                        connector_conversations.c.channel_key == channel_key,
                        connector_conversations.c.persona_id == persona_id,
                    )
                    .values(status="active", last_activity_at=func.now(), updated_at=func.now())
                )
                conversation_id = slot.conversation_id
                resumed = True
            else:
                # Start fresh: a new conversation + an active slot for the named persona.
                conversation_id = f"conv_{uuid.uuid4().hex}"
                conn.execute(
                    pg_insert(conversations_t).values(
                        id=conversation_id, owner_id=owner_id, persona_id=persona_id
                    )
                )
                if slot is not None:
                    # An ENDED slot exists (prior /new or idle-timeout) — reuse it
                    # (the per-persona-per-channel UNIQUE forbids a second row).
                    conn.execute(
                        update(connector_conversations)
                        .where(
                            connector_conversations.c.owner_id == owner_id,
                            connector_conversations.c.platform == platform,
                            connector_conversations.c.channel_key == channel_key,
                            connector_conversations.c.persona_id == persona_id,
                        )
                        .values(
                            conversation_id=conversation_id,
                            status="active",
                            last_activity_at=func.now(),
                            updated_at=func.now(),
                        )
                    )
                else:
                    conn.execute(
                        pg_insert(connector_conversations).values(
                            owner_id=owner_id,
                            platform=platform,
                            channel_key=channel_key,
                            persona_id=persona_id,
                            conversation_id=conversation_id,
                            status="active",
                        )
                    )
                resumed = False

            # Point the channel at the named persona (the flip).
            conn.execute(
                update(connector_channels)
                .where(
                    connector_channels.c.owner_id == owner_id,
                    connector_channels.c.platform == platform,
                    connector_channels.c.channel_key == channel_key,
                )
                .values(active_persona_id=persona_id, updated_at=func.now())
            )
            return ForegroundResult(conversation_id=conversation_id, resumed=resumed)

    def apply_new(self, *, owner_id: str, platform: str, channel_key: str) -> str | None:
        """`/new`: end the active persona's conversation and start a fresh one (§3).

        Per-persona-per-channel: only the *active* persona's slot is reset (others
        untouched). The active persona stays active with a brand-new conversation;
        the old conversation persists as history (``conversations``/``messages``).
        Returns the new conversation id, or ``None`` when no persona is active (a
        ``/new`` with nothing to reset). Owner-scoped + pointer-locked, like the flip.
        """
        with self._owner_scope(owner_id), self._rls.begin() as conn:
            active = conn.execute(
                select(connector_channels.c.active_persona_id)
                .where(
                    connector_channels.c.owner_id == owner_id,
                    connector_channels.c.platform == platform,
                    connector_channels.c.channel_key == channel_key,
                )
                .with_for_update()
            ).scalar()
            if active is None:
                return None
            conversation_id = f"conv_{uuid.uuid4().hex}"
            conn.execute(
                pg_insert(conversations_t).values(
                    id=conversation_id, owner_id=owner_id, persona_id=active
                )
            )
            conn.execute(
                update(connector_conversations)
                .where(
                    connector_conversations.c.owner_id == owner_id,
                    connector_conversations.c.platform == platform,
                    connector_conversations.c.channel_key == channel_key,
                    connector_conversations.c.persona_id == active,
                )
                .values(
                    conversation_id=conversation_id,
                    status="active",
                    last_activity_at=func.now(),
                    updated_at=func.now(),
                )
            )
            return conversation_id

    def sweep_idle_conversations(self, *, now: datetime, idle_after: timedelta) -> int:
        """End every live conversation idle past ``now - idle_after`` (the idle sweep, §3).

        Cross-tenant batch (the A0-worker maintenance pattern) — runs on the
        dispatch (BYPASSRLS) engine, served by the ``(status, last_activity_at)``
        index. Ends each stale slot (``active``/``suspended`` → ``ended``),
        per-persona-per-channel, and clears any channel pointer left pointing at an
        ended slot. Returns the number of conversations ended.

        Raises:
            RuntimeError: No dispatch engine was provided to the store.
        """
        if self._dispatch is None:
            msg = "sweep_idle_conversations requires a dispatch_engine"
            raise RuntimeError(msg)
        cutoff = now - idle_after
        with self._dispatch.begin() as conn:
            ended = conn.execute(
                text(
                    "UPDATE connector_conversations SET status='ended', updated_at=now() "
                    "WHERE status IN ('active','suspended') AND last_activity_at < :cutoff"
                ),
                {"cutoff": cutoff},
            ).rowcount
            # A pointer must never point at an ended slot — clear those.
            conn.execute(
                text(
                    "UPDATE connector_channels c SET active_persona_id=NULL, updated_at=now() "
                    "FROM connector_conversations cc "
                    "WHERE c.owner_id=cc.owner_id AND c.platform=cc.platform "
                    "AND c.channel_key=cc.channel_key AND c.active_persona_id=cc.persona_id "
                    "AND cc.status='ended'"
                )
            )
        return ended
