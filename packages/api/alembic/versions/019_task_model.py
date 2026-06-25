"""The autonomous task model: ``tasks`` + ``task_checkpoints`` tables + RLS (Spec A2, T4).

Adds A2's durable entity above runs. A ``task`` spans days through many bounded agentic
legs (each an A0 job); the ``task_checkpoints`` append-only sequence is the working state
that carries between legs. Both tables are owner-scoped + RLS like every tenant table — the
leg handler sets the owner GUC at job-execution top (D-A0-X-rls-chokepoint) and operates
RLS-as-owner, so a missed scope fails CLOSED (zero rows), never leaks.

**Idempotency — the durable half of A2-R-4.** ``UNIQUE(task_id, checkpoint_seq)`` is the
compare-and-set anchor: a re-delivered leg's duplicate ``(task_id, seq)`` INSERT no-ops via
``ON CONFLICT``, while the ``tasks.head_checkpoint_seq`` CAS
(``UPDATE ... WHERE head_checkpoint_seq IS NOT DISTINCT FROM :predecessor``) advances exactly
once. This pairs with the pure ``Task.advance_checkpoint`` strict-successor check (T2).

**No ``job_dispatcher`` GRANT** (unlike ``schedules``): only A1's cross-tenant tick scan
needed dispatch access. Task/checkpoint access is always owner-scoped under the GUC, matching
the ``synthesis_markers`` precedent (a future A3 cross-tenant stuck-task sweep adds it
additively).

Follows the 012/014 template: tables created from the canonical ``MetaData`` with
``checkfirst=True`` (idempotent — a fresh-install ``001`` ``create_all`` already made them,
since they are now in ``persona_api.db.models``); RLS via explicit SQL with ``DROP POLICY IF
EXISTS`` first; the policy is deliberately NOT added to ``persona_api.db.rls._POLICIES`` (that
drives ``001``'s downgrade, which must not ALTER a table created later) — this migration owns
its full lifecycle, exactly as 009/012/014 do. Manual (``alembic upgrade head``).

**Migration-slot coordination (D-A2-X-migration-placeholder):** developed against placeholder
head ``015_synthesis_markers``; renumbered at merge-back onto the then-current head
``018_add_message_streaming_state`` (the descriptive ``task_model`` name carries; the number
is final). Migrations land one worktree at a time.

Revision ID: 019_task_model
Revises: 018_add_message_streaming_state
"""

from __future__ import annotations

from alembic import op
from persona_api.db.models import task_checkpoints, tasks

revision = "019_task_model"
down_revision = "018_add_message_streaming_state"
branch_labels = None
depends_on = None

_CUR = "current_setting('app.current_user_id', true)"
# Direct owner_id scope, mirrors persona_api.db.rls._POLICIES + migrations 012/014.
# current_setting(..., true) fails CLOSED (an unset GUC yields NULL → matches no row).
_RLS_PREDICATE = f"owner_id = {_CUR}"

_RLS_TABLES = ("tasks", "task_checkpoints")


def upgrade() -> None:
    bind = op.get_bind()
    # Order matters: task_checkpoints FK-references tasks. create() resolves the order
    # from the FK, but create each explicitly for clarity + checkfirst idempotency.
    tasks.create(bind, checkfirst=True)
    task_checkpoints.create(bind, checkfirst=True)
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # DROP IF EXISTS keeps this idempotent + avoids clashing with a policy a
        # fresh-install 001 might already have created (the tables are in metadata).
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(
            f"CREATE POLICY user_isolation ON {table} "
            f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    # Drop the child (FK) table first.
    task_checkpoints.drop(bind, checkfirst=True)
    tasks.drop(bind, checkfirst=True)
