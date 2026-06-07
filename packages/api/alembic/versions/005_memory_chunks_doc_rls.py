"""Extend the ``memory_chunks`` RLS policy for the DocumentStore path (spec 14 + F3 follow-up).

Spec 14 D-14-X-scope-binding-discipline + CSA-1: the ``DocumentStore``
calls ``MemoryStore.write(persona_id=<conversation_id>, ...)`` —
passing the conversation_id into the Protocol's literal ``persona_id``
slot per the calling-convention discipline. The four typed stores
(identity / self_facts / worldview / episodic) continue to pass the
real persona_id.

The Spec 07 RLS policy on ``memory_chunks`` (D-07-5) was written
ASSUMING ``persona_id`` always maps to a real ``personas`` row:
``persona_id IN (SELECT id FROM personas WHERE owner_id = current_user)``.
That subquery returns zero rows when the DocumentStore writes with
``persona_id = conversation_id``, so the WITH CHECK fails and every
document-chunk INSERT raises ``InsufficientPrivilege``.

**The surgical fix.** Add a SECOND permissive policy on
``memory_chunks`` that accepts rows where ``persona_id`` is a
conversation owned by the current user AND ``kind = 'document'``.
Postgres OR-combines multiple PERMISSIVE policies for the same
command class, so the four typed stores keep working unchanged AND
the DocumentStore path becomes legal. The ``kind = 'document'``
gate ensures the conversation-scoped policy can NEVER accidentally
let a non-document row bypass the persona check — a typed-store
write attempting to use a conversation_id-as-persona_id would still
fail the original policy AND fail the new policy (because the typed
stores write with ``kind`` ∈ {identity, self_facts, worldview,
episodic}, never ``document``).

Cross-tenant invariant preserved: both policies fail-closed when
``current_user_id`` is NULL; both restrict to rows owned by the
current user (one via personas.owner_id, one via
conversations.owner_id); neither widens cross-tenant visibility.

Same idempotent pattern as ``002``/``003``/``004``: use
``DROP POLICY IF EXISTS`` + ``CREATE POLICY`` so re-running the
migration on a DB that already has the policy is a no-op-equivalent.
Manual (``alembic upgrade head``), never auto-on-startup (spec 07 §7).

Revision ID: 005_memory_chunks_doc_rls
Revises: 004_add_message_images

Shortened revision id (was ``005_memory_chunks_rls_for_documents``) to
fit Alembic's ``alembic_version.version_num`` column (varchar(32)).
"""

from __future__ import annotations

from alembic import op

revision = "005_memory_chunks_doc_rls"
down_revision = "004_add_message_images"
branch_labels = None
depends_on = None


_CUR = "current_setting('app.current_user_id', true)"

# The new permissive policy. Gated on `kind = 'document'` so it can never
# overlap with the four typed-store kinds. Both USING (read) and
# WITH CHECK (write) carry the same predicate so the policy is symmetric.
_DOC_PREDICATE = (
    f"kind = 'document' AND persona_id IN (SELECT id FROM conversations WHERE owner_id = {_CUR})"
)


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_isolation_documents ON memory_chunks")
    op.execute(
        f"CREATE POLICY user_isolation_documents ON memory_chunks "
        f"USING ({_DOC_PREDICATE}) WITH CHECK ({_DOC_PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_isolation_documents ON memory_chunks")
