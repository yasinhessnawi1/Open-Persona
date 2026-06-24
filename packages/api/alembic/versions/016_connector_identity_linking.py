"""Connector identity linking: link-token + identity tables + RLS (Spec C1, T3).

The identity-mapping security spine (D-C1-5): a one-time link token binds a
platform identity (Telegram/Discord/Slack/WhatsApp/SMS/email account) to a
Persona user; thereafter every inbound message resolves through a live *active*
binding, and Spec 08 ownership/RLS apply unchanged. Two tables, both owner-scoped
and under RLS like every tenant table — the connector process connects as the
``persona_app`` non-superuser role, so a missed scope fails CLOSED (zero rows),
never leaks (the criteria-6/7 guard).

- ``connector_link_tokens`` — the one-time handshake token. Only the sha256 hex of
  the bearer token is stored (``token_hash``); a DB leak must not yield usable
  tokens (the BYO-Fernet at-rest posture). Single-use via the pending→consumed
  status transition; short-TTL via ``expires_at``.
- ``connector_identities`` — the platform-identity ↔ Persona-user binding. The
  PARTIAL unique index ``(platform, platform_identity) WHERE status='active'``
  enforces one ACTIVE owner per identity (the cross-user-breach guard) while
  keeping revoked rows for audit and allowing re-link after unlink.

Follows the ``012`` template: tables created from the canonical ``MetaData`` with
``checkfirst=True`` (idempotent — a fresh-install ``001`` ``create_all`` already
made them, since they're now in ``persona_api.db.models``); RLS via explicit SQL
with ``DROP POLICY IF EXISTS`` before ``CREATE`` (idempotent re-run / fresh-DB
overlap). Manual (``alembic upgrade head``), never auto-on-startup. The RLS
policies are deliberately NOT in ``persona_api.db.rls._POLICIES`` (that drives
``001``'s downgrade, which must not ALTER a table created later); this migration
owns their lifecycle, exactly as ``009``/``012`` do.

**Migration-slot coordination (D-C1-X-migrations):** developed off-trunk on the
C-track with a PLACEHOLDER ``down_revision`` = the worktree base head. The
orchestrator linearises + re-points at merge-back (A1/K2 also have pending
migrations). Do NOT self-number (R-19-1).

Revision ID: 016_connector_identity_linking
Revises: 015_synthesis_markers
"""

from __future__ import annotations

from alembic import op
from persona_api.db.models import connector_identities, connector_link_tokens

revision = "016_connector_identity_linking"
down_revision = "015_synthesis_markers"
branch_labels = None
depends_on = None

_CUR = "current_setting('app.current_user_id', true)"

# (table, USING/WITH CHECK predicate) — direct owner_id scope, mirrors
# persona_api.db.rls._POLICIES. current_setting(..., true) fails CLOSED (an unset
# GUC yields NULL, which matches no row). The cross-tenant pre-auth resolve/redeem
# reads run on the dispatch (BYPASSRLS) engine, by design (the A0-worker pattern);
# every owner-scoped write/list here is RLS-confined.
_RLS: tuple[tuple[str, str], ...] = (
    ("connector_link_tokens", f"owner_id = {_CUR}"),
    ("connector_identities", f"owner_id = {_CUR}"),
)


def upgrade() -> None:
    bind = op.get_bind()
    # No FK ordering between them (both reference users); the partial-active unique
    # index + the owner index ride the .create() (they're declared on the Table).
    connector_link_tokens.create(bind, checkfirst=True)
    connector_identities.create(bind, checkfirst=True)
    for table, predicate in _RLS:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # DROP IF EXISTS keeps this idempotent + avoids clashing with a policy a
        # fresh-install 001 might already have created (the tables are in metadata).
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
    connector_identities.drop(bind, checkfirst=True)
    connector_link_tokens.drop(bind, checkfirst=True)
