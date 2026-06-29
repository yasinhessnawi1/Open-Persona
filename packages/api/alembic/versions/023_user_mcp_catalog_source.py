"""Add ``user_mcp_servers.catalog_source`` — self-extension adoption provenance (Spec N4, N4-D-9).

N4 lets a persona adopt an app from the mirrored catalog, reusing the Spec-30 BYO store
(``user_mcp_servers``) for the per-user credential. This migration adds ONE additive,
nullable column — ``catalog_source TEXT NULL`` — recording the catalog entry name an
adoption came from (e.g. ``notion-remote``); ``NULL`` means a manually-added BYO server.
It supports the N4-D-6 vetted-set check and an honest "self-extended" UI marker. It
carries **no secret** — provenance metadata only.

Additive + backward-compatible: the pre-N4 BYO path is byte-identical with this column
``NULL``. No backfill (NULL is the correct value for every pre-N4 manual BYO row), no
NOT NULL, no CHECK (the value is free-form: NULL or a catalog name). Inherits the existing
``user_mcp_servers`` RLS policy (migration 009) — no new policy DDL.

Split-home discipline (cf. migrations 010 / 013 / 018 / 020): the column is also declared
on the canonical ``persona_api.db.models.user_mcp_servers`` table, so on a fresh DB
``001_initial``'s ``metadata.create_all`` already builds it and the guarded DDL below is a
harmless no-op; on a previously-deployed DB it actually adds it. Both agree.

Revision ID: 023_user_mcp_catalog_source
Revises: 022_approvals_permissions
"""

from __future__ import annotations

from alembic import op

# PLACEHOLDER down_revision (R-19-1 chain numbering): chained off main's current head at
# authoring time, ``022_approvals_permissions`` (A3). N4 is a leaf with one migration;
# other in-flight specs (C3 / R-track) may land migrations before N4 merges, so this number
# + ``down_revision`` are RECOMPUTED at merge-back to preserve the single-head chain — do
# NOT rely on ``023`` / ``022`` surviving verbatim.
revision = "023_user_mcp_catalog_source"
down_revision = "022_approvals_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive nullable column — never locks, never rewrites the table. Idempotent
    # (``IF NOT EXISTS``) so a fresh DB where ``001`` already created it is a no-op.
    op.execute("ALTER TABLE user_mcp_servers ADD COLUMN IF NOT EXISTS catalog_source TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE user_mcp_servers DROP COLUMN IF EXISTS catalog_source")
