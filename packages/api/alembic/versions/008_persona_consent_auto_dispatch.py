"""Add tri-state auto-dispatch consent to ``personas`` (spec 21 T09, D-21-7).

Two nullable columns capture the consent tri-state that a bare boolean cannot:

- ``consent_to_auto_dispatch BOOLEAN NULL`` — ``NULL`` = never asked /
  revoked-back-to-ask (prompt on next persona-initiated dispatch), ``TRUE`` =
  granted (auto-dispatch), ``FALSE`` = explicitly declined (a stable preference;
  never auto-re-prompts — D-21-17; recovery is via the settings toggle only,
  which resets to ``NULL``).
- ``consent_updated_at TIMESTAMPTZ NULL`` — when the consent state last changed
  (every transition also emits an ``AuditEvent`` at the route).

Additive + nullable, so every existing persona is byte-for-byte unaffected and
loads as ``NULL`` (never asked) — the first *autonomous* task prompts once
(D-21-13: explicit user-initiated runs are never consent-gated).

Revision ID: 008_persona_consent_dispatch
Revises: 007_memory_chunks_persona_fk
Create Date: 2026-06-13

Note: revision ID kept at 28 chars to fit alembic_version VARCHAR(32) cap;
filename retains the longer descriptive form (no functional impact since
alembic stores the `revision` constant, not the filename, in alembic_version).
"""

from __future__ import annotations

from alembic import op

revision = "008_persona_consent_dispatch"
down_revision = "007_memory_chunks_persona_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``ADD COLUMN IF NOT EXISTS`` per the 003/004 precedent: ``001_initial``
    # builds the schema via ``MetaData.create_all`` from the *live* models, so on
    # a fresh DB these columns already exist when ``008`` runs (harmless no-op);
    # on a DB that ran ``001`` before spec 21 the columns are genuinely added.
    op.execute("ALTER TABLE personas ADD COLUMN IF NOT EXISTS consent_to_auto_dispatch BOOLEAN")
    op.execute("ALTER TABLE personas ADD COLUMN IF NOT EXISTS consent_updated_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE personas DROP COLUMN IF EXISTS consent_updated_at")
    op.execute("ALTER TABLE personas DROP COLUMN IF EXISTS consent_to_auto_dispatch")
