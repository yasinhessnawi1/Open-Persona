"""Drop the ``memory_chunks.persona_id`` FK to ``personas.id``.

v0.1.1 patch — surfaces the 4th integration gap T16 exposed:
migration 005 added the document-RLS aux policy that legitimately allows
``memory_chunks`` rows where ``persona_id`` carries a ``conversations.id``
value (per the gate ``persona_id IN (SELECT id FROM conversations WHERE
owner_id = current_user_id)``). The 001-era FK ``memory_chunks_persona_id_fkey``
to ``personas.id`` blocks every legitimate document write because
``conversations.id`` is not in the ``personas`` table.

The ``memory_chunks.persona_id`` column now carries discriminated semantics
gated by ``kind``: for ``identity`` / ``self_facts`` / ``worldview`` /
``episodic`` rows the value is a ``personas.id``; for ``document`` rows it
is a ``conversations.id``. PostgreSQL cannot express conditional foreign
keys directly without a trigger; the RLS policy + CHECK constraint
provide the integrity guarantees the FK previously enforced for the
non-document path, and document rows are gated by the migration-005 aux
policy. Drop the constraint.

Revision ID: 007_memory_chunks_persona_fk
Revises: 006_memory_chunks_kind_doc
Create Date: 2026-06-10
"""

from __future__ import annotations

from alembic import op

revision = "007_memory_chunks_persona_fk"
down_revision = "006_memory_chunks_kind_doc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_chunks DROP CONSTRAINT IF EXISTS memory_chunks_persona_id_fkey")


def downgrade() -> None:
    # No-op by design. Restoring the FK is unsafe in two directions:
    # (1) on a database with kind='document' rows the ADD CONSTRAINT would
    #     raise ForeignKeyViolation (those rows carry conversations.id values
    #     that are not in personas);
    # (2) the FK is not declared in the SQLAlchemy ``db/models.py`` schema
    #     anymore (v0.1.1 patch removed the inline ``ForeignKey`` to match
    #     the post-007 shape), so 001's ``_schema.drop_all`` cannot order
    #     drops correctly if the raw-SQL-added FK is present — surfaces as
    #     DependentObjectsStillExist on the personas table.
    # The intentional asymmetry: ``007.upgrade`` drops the FK once, going
    # forward; ``007.downgrade`` does nothing. The pre-007 schema is no
    # longer reachable from a v0.1.1+ deployment.
    pass
