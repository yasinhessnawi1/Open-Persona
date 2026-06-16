"""Bring-your-own MCP: ``user_mcp_servers`` + ``persona_mcp_assignments`` (spec 30, D-30-3/6).

The first migration since ``008``. Adds the two tables for user-owned MCP
servers (Cluster C) plus their row-level-security policies, mirroring the
Spec-07 ``001_initial`` pattern (tables from the canonical ``MetaData``, RLS via
explicit SQL — D-07-5/6) and the idempotent ``DROP POLICY IF EXISTS`` discipline
of ``005``.

- ``user_mcp_servers`` — user-scoped (owner_id), RLS ``owner_id = current_user``.
  Credentials are encrypted at rest (Fernet, T07); the column never holds
  plaintext. The user-supplied ``url`` is SSRF-validated at the route + at
  connect (T08) — the DB stores it, the runtime never trusts it.
- ``persona_mcp_assignments`` — persona ↔ server join (D-30-6), RLS scoped
  through the persona's owner.

Idempotent: tables created with ``checkfirst=True`` (no-op if a fresh install's
``001`` ``create_all`` already made them — the two tables are now in the canonical
metadata); each policy ``DROP ... IF EXISTS`` before ``CREATE`` so re-running, or
running after ``001``'s ``upgrade_rls_sql`` already created the policy on a fresh
DB, is a no-op-equivalent. Manual (``alembic upgrade head``), never auto-on-startup.

Revision ID: 009_user_mcp_servers
Revises: 008_persona_consent_dispatch
"""

from __future__ import annotations

from alembic import op
from persona_api.db.models import persona_mcp_assignments, user_mcp_servers

revision = "009_user_mcp_servers"
down_revision = "008_persona_consent_dispatch"
branch_labels = None
depends_on = None

_CUR = "current_setting('app.current_user_id', true)"

# (table, USING/WITH CHECK predicate) — mirrors persona_api.db.rls._POLICIES.
_RLS: tuple[tuple[str, str], ...] = (
    ("user_mcp_servers", f"owner_id = {_CUR}"),
    (
        "persona_mcp_assignments",
        f"persona_id IN (SELECT id FROM personas WHERE owner_id = {_CUR})",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    # FK order: servers first, then the assignment join that references it.
    user_mcp_servers.create(bind, checkfirst=True)
    persona_mcp_assignments.create(bind, checkfirst=True)
    for table, predicate in _RLS:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # DROP IF EXISTS keeps this idempotent + avoids clashing with the policy
        # a fresh-install 001 already created (the tables are now in metadata).
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
    persona_mcp_assignments.drop(bind, checkfirst=True)
    user_mcp_servers.drop(bind, checkfirst=True)
