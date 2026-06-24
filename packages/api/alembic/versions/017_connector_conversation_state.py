"""Connector conversation state: channels pointer + per-persona conversations + RLS (Spec C1, T5).

The per-persona parallel-conversation model (§3, the agentic-future linchpin):
each persona has at most one active conversation per user per channel; switching
personas suspends — never ends — the previously-active one. Two additive
owner-scoped tables, both under RLS like every tenant table (the connector
process connects as ``persona_app``; a missed scope fails CLOSED).

- ``connector_channels`` — the active-persona pointer (one row per (owner,
  platform, channel); the ``SELECT … FOR UPDATE`` flip target, D-C1-2).
- ``connector_conversations`` — per-persona parallel conversations, each FK'd to a
  real ``conversations`` row (additive — NO column on ``conversations``, K2-safe);
  the partial-free UNIQUE ``(owner, platform, channel, persona)`` is the parallel
  invariant.

Follows the ``012`` template: tables from the canonical ``MetaData`` with
``checkfirst=True``; RLS via explicit SQL with ``DROP POLICY IF EXISTS`` first;
policies NOT in ``persona_api.db.rls._POLICIES`` (this migration owns them, as
``009``/``012`` do). Manual (``alembic upgrade head``), never auto-on-startup.

**Migration-slot coordination (D-C1-X-migrations):** C1's SECOND migration. It
chains off C1's first (``connector_identity_linking``) so the worktree keeps a
single linear head (two migrations on ``013`` would branch into multiple heads).
The orchestrator re-points the consecutive C1 pair + assigns final ``0NN_``
numbers at merge-back (A1/K2 also pending). Do NOT self-number (R-19-1).

Revision ID: 017_connector_conversation_state
Revises: 016_connector_identity_linking
"""

from __future__ import annotations

from alembic import op
from persona_api.db.models import connector_channels, connector_conversations

revision = "017_connector_conversation_state"
down_revision = "016_connector_identity_linking"
branch_labels = None
depends_on = None

_CUR = "current_setting('app.current_user_id', true)"

_RLS: tuple[tuple[str, str], ...] = (
    ("connector_channels", f"owner_id = {_CUR}"),
    ("connector_conversations", f"owner_id = {_CUR}"),
)


def upgrade() -> None:
    bind = op.get_bind()
    # connector_conversations references connector_channels? No — both reference
    # users/personas/conversations only; order is independent.
    connector_channels.create(bind, checkfirst=True)
    connector_conversations.create(bind, checkfirst=True)
    for table, predicate in _RLS:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(
            f"CREATE POLICY user_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table, _ in _RLS:
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    connector_conversations.drop(bind, checkfirst=True)
    connector_channels.drop(bind, checkfirst=True)
