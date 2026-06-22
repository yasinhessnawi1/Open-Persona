"""Add the ``messages.originated`` discriminator column (Spec C0, D-C0-X-discriminator DB half).

Marks a message a persona produced with **no preceding user turn** — an originated
(persona-initiated) message. ``role`` stays ``'assistant'`` (orthogonal axis — the
``messages_role_check`` CHECK is untouched, no ``'persona'`` value); this boolean is
the persisted *who-initiated* discriminator and the queryable source of truth. The
persona-api boundary maps it to/from the in-core ``metadata["originated"]`` marker
(the two-layer discriminator).

Idempotent: ``001_initial`` builds the schema via ``metadata.create_all`` from the
current canonical models (which now declare ``originated``), so on a freshly-built
DB the column already exists and ``ADD COLUMN IF NOT EXISTS`` is a harmless no-op;
on a previously-deployed DB it actually adds the column. ``NOT NULL DEFAULT false``
→ every historical row reads ``false`` (correct: they were all solicited). The
downgrade drops it. The source-of-truth column is also declared on the canonical
``persona_api.db.models.messages`` table so the schema and the migration agree (the
split-home discipline, per migration 010's template).

Revision ID: 013_add_message_originated
Revises: 012_jobs_queue
"""

from __future__ import annotations

from alembic import op

revision = "013_add_message_originated"
# Re-pointed at merge-back to follow A0's ``012_jobs_queue`` so the canonical chain
# is linear: 010 → 011_knowledge_graph → 012_jobs_queue → 013_add_message_originated
# (D-C0-X-migration — C0 was the only pending migration, so 013-on-012 is the locked
# target). R-19-1 owns chain numbering, audited against the filesystem.
down_revision = "012_jobs_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS originated BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS originated")
