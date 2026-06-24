"""Add the ``messages`` streaming-lifecycle columns (Spec P1, D-P1-checkpoint).

The chat turn becomes a **detached, resumable session** (P1): it runs in a
background task, is checkpointed AS it streams, and reattaches on return. This
migration adds the persisted streaming lifecycle to the ``messages`` table:

- ``streaming_status TEXT NULL`` — the in-progress assistant row's lifecycle:
  ``running`` while streaming, then a terminal value (``complete`` /
  ``cancelled`` / ``error`` / ``interrupted``). **NULL = a legacy / non-streamed
  row** (every historical message + every non-P1 write path) → renders as a
  plain final message (the ``tier_used`` / ``originated`` nullable-additive
  precedent, clean degrade).
- ``stream_events JSONB NULL`` — the partial event-log (text deltas + tool
  events, same shape as ``runs.steps``) so a reattach-after-gap reconstructs the
  tool/text interleave, not just the final text. NULL for legacy / text-only.

Both are **DB-persistence state ONLY** — they are NOT ``ConversationMessage``
model fields (the C0 lesson: a top-level model field would stamp every solicited
message and break the Spec-13 byte-for-byte dump corpus).

Plus the **one-active-turn DB guarantee** (D-P1-one-active-turn): a partial
UNIQUE index over the in-flight rows
(``messages(conversation_id) WHERE streaming_status='running'``) — exactly one
streaming turn per conversation, backstopping the in-process
``ChatTurnRegistry`` check against a race. Inherits the existing ``messages``
RLS policy — no new policy DDL.

Idempotent: ``001_initial`` builds the schema via ``metadata.create_all`` from
the current canonical models (which now declare these columns + the CHECK + the
partial index), so on a freshly-built DB they already exist and the guarded DDL
below is a harmless no-op; on a previously-deployed DB it actually adds them.
The source-of-truth columns/constraints are also declared on the canonical
``persona_api.db.models.messages`` table so the schema and the migration agree
(the split-home discipline, per migrations 010 / 013).

Revision ID: 018_add_message_streaming_state
Revises: 017_connector_conversation_state
"""

from __future__ import annotations

from alembic import op

# Linearized at merge-back (R-19-1 chain numbering): P1's streaming-state
# migration lands as 018, chained off C1's head 017_connector_conversation_state
# (single-head discipline; one worktree lands a migration at a time).
revision = "018_add_message_streaming_state"
down_revision = "017_connector_conversation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS streaming_status TEXT")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS stream_events JSONB")
    # Guarded CHECK (ADD CONSTRAINT has no IF NOT EXISTS; create_all may already
    # have added it on a fresh DB).
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'messages_streaming_status_check'
          ) THEN
            ALTER TABLE messages ADD CONSTRAINT messages_streaming_status_check
              CHECK (
                streaming_status IS NULL
                OR streaming_status IN ('running', 'complete', 'cancelled', 'interrupted', 'error')
              );
          END IF;
        END $$;
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_one_streaming_per_conversation "
        "ON messages (conversation_id) WHERE streaming_status = 'running'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_messages_one_streaming_per_conversation")
    op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_streaming_status_check")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS stream_events")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS streaming_status")
