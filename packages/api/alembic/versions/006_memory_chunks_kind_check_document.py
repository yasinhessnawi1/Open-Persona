"""Extend the ``memory_chunks.kind`` CHECK constraint to accept ``'document'``.

Spec 19 D-19-X-memory-chunks-kind-check-migration (chain entry 23) — a
MID-FLIGHT LAND discovery surfaced by the F5 T16 production-shape
integration test.

Migration ``005_memory_chunks_doc_rls`` added a permissive RLS policy
that gates ``memory_chunks`` writes on ``kind = 'document'`` so the
:class:`~persona.stores.document_store.DocumentStore` path (which writes
with ``persona_id = conversation_id``) becomes legal at the RLS layer.

But the table-level CHECK constraint on ``kind`` (defined in
``001_initial`` via the canonical ``persona_api.db.models`` MetaData)
still restricts values to the four typed-store kinds:
``('identity', 'self_facts', 'worldview', 'episodic')``. A document
INSERT lands on the CHECK *before* RLS evaluates, so every
``DocumentStore.write`` raises ``IntegrityError`` —
``new row for relation "memory_chunks" violates check constraint
"memory_chunks_kind_check"``. Real users uploading documents see the
persistence step blow up at the DB layer.

**The surgical fix.** Drop the old constraint, recreate it with
``'document'`` added to the accepted set. Additive, additive only: every
historical row already in the table satisfies the new predicate (the
new set is a strict superset of the old), so the ALTER is a no-op for
data and a one-shot DDL change for the schema. No data migration, no
backfill, no read-side change.

Idempotence pattern (matches ``002``/``003``/``004``/``005``):
``DROP CONSTRAINT IF EXISTS`` + ``ADD CONSTRAINT`` so re-running on a DB
that already has the new shape is a no-op-equivalent. Manual
(``alembic upgrade head``), never auto-on-startup (spec 07 §7).

The canonical ``persona_api.db.models.memory_chunks`` CheckConstraint
declaration is updated in lockstep so the split-home discipline holds:
the model is the source of truth for ``001`` (fresh DB
``metadata.create_all``), and ``006`` is the migration of record for
existing deployments.

Downgrade: revert to the spec-07 four-kind shape. **WARNING:** if any
``kind = 'document'`` rows exist when ``downgrade`` runs, the
``ADD CONSTRAINT`` will fail because the existing data violates the
predicate. Operators must delete document chunks first, e.g.
``DELETE FROM memory_chunks WHERE kind = 'document';``. This matches
the standard alembic-with-data warning — there is no clean reversal
once production data has been written under the new shape.

Revision ID: 006_memory_chunks_kind_doc
Revises: 005_memory_chunks_doc_rls
"""

from __future__ import annotations

from alembic import op

revision = "006_memory_chunks_kind_doc"
down_revision = "005_memory_chunks_doc_rls"
branch_labels = None
depends_on = None


_NEW_KINDS = "('identity', 'self_facts', 'worldview', 'episodic', 'document')"
_OLD_KINDS = "('identity', 'self_facts', 'worldview', 'episodic')"


def upgrade() -> None:
    op.execute("ALTER TABLE memory_chunks DROP CONSTRAINT IF EXISTS memory_chunks_kind_check")
    op.execute(
        f"ALTER TABLE memory_chunks ADD CONSTRAINT memory_chunks_kind_check "
        f"CHECK (kind IN {_NEW_KINDS})"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE memory_chunks DROP CONSTRAINT IF EXISTS memory_chunks_kind_check")
    op.execute(
        f"ALTER TABLE memory_chunks ADD CONSTRAINT memory_chunks_kind_check "
        f"CHECK (kind IN {_OLD_KINDS})"
    )
