"""Synthesis idempotency marker: ``synthesis_markers`` + RLS (Spec K2, T8).

The per-interaction high-water-mark that makes synthesis idempotent (criterion 8,
the second line behind A0's idempotency key): synthesis processes only content past
``synthesised_up_to``, then advances it in the same owner-scoped transaction.
Channel-agnostic (conversation / agentic_run / voice) so all three feeders share
one surface (the C1 forward seam).

Owner-scoped + under RLS like every tenant table — the worker connects as the
``persona_app`` non-superuser role, so a missed scope fails CLOSED (zero rows).
Follows the ``012`` template: table created from the canonical ``MetaData`` with
``checkfirst=True`` (idempotent — a fresh-install ``001`` ``create_all`` already
made it), RLS via explicit SQL with ``DROP POLICY IF EXISTS`` before ``CREATE``.
The RLS policy is deliberately NOT added to ``persona_api.db.rls._POLICIES`` (that
drives ``001``'s downgrade, which must not ALTER a table created later); this
migration owns its full lifecycle, exactly as ``009`` / ``011`` / ``012`` do.

**Migration-slot coordination:** developed off-trunk on the K-track with a
placeholder ``down_revision`` and linearized at merge-back behind A1's
``014_schedules`` (which follows C0's ``013_add_message_originated``).

Revision ID: 015_synthesis_markers
Revises: 014_schedules
"""

from __future__ import annotations

from alembic import op
from persona_api.db.models import synthesis_markers

revision = "015_synthesis_markers"
down_revision = "014_schedules"
branch_labels = None
depends_on = None

_CUR = "current_setting('app.current_user_id', true)"

# Direct owner_id scope, mirrors persona_api.db.rls._POLICIES. current_setting(...,
# true) fails CLOSED (an unset GUC yields NULL, which matches no row).
_RLS_PREDICATE = f"owner_id = {_CUR}"


def upgrade() -> None:
    bind = op.get_bind()
    synthesis_markers.create(bind, checkfirst=True)
    op.execute("ALTER TABLE synthesis_markers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE synthesis_markers FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS user_isolation ON synthesis_markers")
    op.execute(
        "CREATE POLICY user_isolation ON synthesis_markers "
        f"USING ({_RLS_PREDICATE}) WITH CHECK ({_RLS_PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_isolation ON synthesis_markers")
    op.execute("ALTER TABLE synthesis_markers NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE synthesis_markers DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS synthesis_markers")
