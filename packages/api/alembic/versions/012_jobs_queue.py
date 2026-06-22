"""Durable job queue: ``jobs`` + ``jobs_archive`` + RLS (Spec A0, T2).

Adds the Postgres-backed job queue (``SELECT … FOR UPDATE SKIP LOCKED``) that
replaces Spec 08's in-process ``asyncio.Task`` substrate for background work.
Two tables, both owner-scoped and under RLS like every tenant table — the new
worker service connects as the ``persona_app`` non-superuser role, so a missed
scope fails CLOSED (zero rows), never leaks.

- ``jobs`` — the hot queue. ``fillfactor=80`` + aggressive per-table autovacuum
  (D-A0-4 hygiene against the known Postgres-as-queue bloat slow-death). The
  ``idx_jobs_claim`` partial index ``(priority DESC, scheduled_at) WHERE
  state='queued'`` serves the claim predicate; ``idx_jobs_lease_expiry`` serves
  the rescuer's lease-reclaim sweep. ``idempotency_key`` is UNIQUE so a duplicate
  enqueue is a no-op.
- ``jobs_archive`` — the cold table terminal jobs age into (cleaner sweep), so
  the hot working set stays tiny while A3/A6 still read history.

Follows the ``009`` template: tables created from the canonical ``MetaData`` with
``checkfirst=True`` (idempotent — a fresh-install ``001`` ``create_all`` already
made them, since they're now in ``persona_api.db.models``), RLS via explicit SQL
with ``DROP POLICY IF EXISTS`` before ``CREATE`` (idempotent re-run / fresh-DB
overlap). Manual (``alembic upgrade head``), never auto-on-startup.

The RLS policies are deliberately NOT added to ``persona_api.db.rls._POLICIES``
(that drives ``001``'s downgrade, which must not ALTER a table created later);
this migration owns their full lifecycle, exactly as ``009`` does for the MCP
tables.

**Migration-slot coordination (D-A0-X-migration-placeholder):** at merge-back the
chain was linearised behind the K0 graph-store migration, so this migration now
revises ``011_knowledge_graph`` (chain: ``010 → 011_knowledge_graph →
012_jobs_queue``). It originally carried the ``010`` placeholder ``down_revision``
while developed off-trunk on the A-track.

Revision ID: 012_jobs_queue
Revises: 011_knowledge_graph
"""

from __future__ import annotations

from alembic import op
from persona_api.db.models import jobs, jobs_archive

revision = "012_jobs_queue"
down_revision = "011_knowledge_graph"
branch_labels = None
depends_on = None

_CUR = "current_setting('app.current_user_id', true)"

# (table, USING/WITH CHECK predicate) — direct owner_id scope, mirrors
# persona_api.db.rls._POLICIES. current_setting(..., true) fails CLOSED (an unset
# GUC yields NULL, which matches no row).
_RLS: tuple[tuple[str, str], ...] = (
    ("jobs", f"owner_id = {_CUR}"),
    ("jobs_archive", f"owner_id = {_CUR}"),
)


def upgrade() -> None:
    bind = op.get_bind()
    # FK order is independent (both reference users); create hot then cold.
    jobs.create(bind, checkfirst=True)
    jobs_archive.create(bind, checkfirst=True)
    # Claim-time fairness index (T7) — explicit so it lands on an already-created
    # jobs table too (``.create(checkfirst=True)`` skips a table that exists and
    # would not add a new index). Idempotent.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_inflight_by_owner "
        "ON jobs (owner_id) WHERE state IN ('claimed', 'running')"
    )
    # D-A0-4 hygiene against the Postgres-as-queue bloat slow-death: fillfactor=80
    # so status updates stay HOT (no index churn), and aggressive per-table
    # autovacuum so the hot table's dead tuples are reclaimed promptly.
    # scale_factor=0.05 is a starting default the A0-R-3 soak validates; tune
    # toward 0.01 if the dead-tuple slope climbs. (Postgres-only, like the RLS
    # SQL below — the migration chain assumes the cloud Postgres path.)
    op.execute(
        "ALTER TABLE jobs SET ("
        "fillfactor = 80, "
        "autovacuum_vacuum_scale_factor = 0.05, "
        "autovacuum_vacuum_threshold = 100, "
        "autovacuum_vacuum_cost_limit = 2000)"
    )
    for table, predicate in _RLS:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # DROP IF EXISTS keeps this idempotent + avoids clashing with a policy a
        # fresh-install 001 might already have created (the tables are in metadata).
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(
            f"CREATE POLICY user_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )
    # D-A0-X-rls-chokepoint hardening seam: if a least-privilege ``job_dispatcher``
    # role has been provisioned out-of-band (D-07-5 — roles are NOT created in
    # migrations), grant it the cross-tenant jobs-table access the worker's
    # dispatch engine needs and nothing else (no tenant-table grants). Conditional
    # + idempotent, so a DB without the role is unaffected. The role's BYPASSRLS
    # attribute (so it sees all tenants' jobs to dispatch) is set at provisioning.
    # Least-privilege grant (security review T4): the dispatch path SELECTs +
    # UPDATEs jobs (claim/heartbeat/complete/reclaim) and DELETEs from jobs +
    # INSERTs/DELETEs jobs_archive (the archiver/retention sweeps). It does NOT
    # INSERT into jobs (enqueue uses the owner-scoped RLS engine) and does NOT
    # SELECT jobs_archive (tenant-facing history reads go through the RLS engine).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'job_dispatcher') THEN
                GRANT SELECT, UPDATE, DELETE ON jobs TO job_dispatcher;
                GRANT INSERT, DELETE ON jobs_archive TO job_dispatcher;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table, _ in _RLS:
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    jobs_archive.drop(bind, checkfirst=True)
    jobs.drop(bind, checkfirst=True)
