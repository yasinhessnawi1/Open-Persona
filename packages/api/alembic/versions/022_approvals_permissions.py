"""The A3 approval artifacts: ``approval_proposals`` + ``approval_decisions`` + RLS (Spec A3, T5).

Adds the durable side of the approval spine. A gated leg records an exact :class:`ActionProposal`
(the tool + verbatim arguments, replayed verbatim on approval — never re-derived); the task
parks ``waiting(on_user)`` (A2); the user's reply becomes an append-only :class:`ApprovalDecision`
(verbatim reply + channel — the criterion-9 audit trail). Both tables are owner-scoped + RLS like
every tenant table — a missed scope fails CLOSED (zero rows), never leaks.

**The structural one-pending-per-task guarantee (criterion 4).** A partial unique index
``(task_id) WHERE status='pending'`` makes "two open proposals on one task" *impossible* at the
DB, not merely checked — the disambiguation lean ("ok" has exactly one referent). 'pending' is
the sole open state; a material re-confirm updates the row in place.

**No budget / policy DDL.** Budget lifecycle rides ``audit_log`` rows (``action='budget.*'``) and
the cap is the contract's ``ContractBounds.total_budget_micros`` + SUM of ``budget.extended`` rows
(the A1 audit-projection precedent); the per-task policy matrix rides ``tasks.contract_json``
(no column). So this migration is exactly the two approval tables (A3-D-X-migration).

Follows the 019 template: tables created from the canonical ``MetaData`` with ``checkfirst=True``
(idempotent); RLS via explicit SQL with ``DROP POLICY IF EXISTS`` first; the policy is deliberately
NOT added to ``persona_api.db.rls._POLICIES`` (that drives 001's downgrade, which must not ALTER a
table created later) — this migration owns its full lifecycle, as 009/012/014/019 do. Manual
(``alembic upgrade head``).

**Migration-slot coordination (A3-D-X-migration / D-A2-X-migration-placeholder):** developed
against placeholder head ``019_task_model``; the orchestrator re-points ``down_revision`` onto the
then-current head and renumbers at merge-back (the descriptive ``approvals_permissions`` name
carries; the number is final there). Migrations land one worktree at a time.

Revision ID: 022_approvals_permissions
Revises: 021_add_calls_table
"""

from __future__ import annotations

from alembic import op
from persona_api.db.models import (
    approval_decisions,
    approval_proposals,
    cadence_counters,
    platform_controls,
    suspended_personas,
)

revision = "022_approvals_permissions"
down_revision = "021_add_calls_table"
branch_labels = None
depends_on = None

_CUR = "current_setting('app.current_user_id', true)"
# Direct owner_id scope, mirrors persona_api.db.rls._POLICIES + migrations 012/014/019.
# current_setting(..., true) fails CLOSED (an unset GUC yields NULL → matches no row).
_RLS_PREDICATE = f"owner_id = {_CUR}"

# suspended_personas + cadence_counters are owner-scoped + RLS; platform_controls is
# operational (NO RLS).
_RLS_TABLES = (
    "approval_proposals",
    "approval_decisions",
    "suspended_personas",
    "cadence_counters",
)


def upgrade() -> None:
    bind = op.get_bind()
    # Parent first: approval_decisions FK-references approval_proposals.
    approval_proposals.create(bind, checkfirst=True)
    approval_decisions.create(bind, checkfirst=True)
    # The kill-switch tables (T11): persona-suspend (owner-scoped) + the global control flag.
    suspended_personas.create(bind, checkfirst=True)
    platform_controls.create(bind, checkfirst=True)
    # The cadence cap counter (T12, owner-scoped).
    cadence_counters.create(bind, checkfirst=True)
    # The remind-once marker (A3-D-2, T9). Idempotent ADD COLUMN so an already-created table
    # (a DB built before this column existed) gains it too (the 002/004/010 pattern); a fresh
    # create() above already includes it from the model.
    op.execute("ALTER TABLE approval_proposals ADD COLUMN IF NOT EXISTS reminded_at TIMESTAMPTZ")
    # The A3 budget effective-cap read sums budget.extended audit rows per task on the
    # leg-boundary check (T10); index (target, action) on the existing audit_log so that
    # lookup never re-scans the table. Additive + idempotent on the pre-existing 001 table.
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_target_action ON audit_log (target, action)")
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE so the owning (non-superuser) app role cannot bypass its own RLS.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        # USING bars reading others' rows; WITH CHECK bars inserting a row owned by another.
        op.execute(
            f"CREATE POLICY user_isolation ON {table} "
            f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    # The audit index is on the persistent audit_log table — drop it explicitly (the approval
    # tables + their reminded_at column drop with the tables below).
    op.execute("DROP INDEX IF EXISTS idx_audit_target_action")
    for table in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    # Drop the cadence + kill-switch tables (their RLS dropped in the loop above).
    cadence_counters.drop(bind, checkfirst=True)
    platform_controls.drop(bind, checkfirst=True)
    suspended_personas.drop(bind, checkfirst=True)
    # Drop the child (FK) table first.
    approval_decisions.drop(bind, checkfirst=True)
    approval_proposals.drop(bind, checkfirst=True)
