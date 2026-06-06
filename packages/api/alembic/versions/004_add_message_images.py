"""Add the nullable ``messages.images`` JSONB column (spec 13, D-13-X-now option c).

Spec 13 Phase 5 follow-up (T20). The image-bearing message path widens the
chat boundary: ``PostMessageRequest`` gains an ``images: list[ImageRef]`` field,
the conversations route constructs a multimodal ``ConversationMessage`` whose
``content`` is ``list[MessageContent]`` (TextContent + N ImageContent blocks),
and ``chat_service._persist_turn`` writes the image references as JSONB on the
``messages.images`` column added here. Image bytes live exactly once under the
persona's Spec 03 workspace (D-13-4); the row carries only ``workspace_path``
and ``media_type`` per reference so the messages table is bounded by reference
count, not by image bytes (Dominant Concern #2).

Same idempotent pattern as ``002``/``003``: ``001_initial`` builds the schema
via ``metadata.create_all`` from the *current* canonical models (which now
declare ``images``), so on a fresh DB the column already exists when ``004``
runs and ``ADD COLUMN IF NOT EXISTS`` is a harmless no-op; on a DB that ran
``001`` before this column was declared, ``004`` actually adds it. ``004`` is
the migration of record for ``messages.images``. Manual
(``alembic upgrade head``), never auto-on-startup (spec 07 §7).

Revision ID: 004_add_message_images
Revises: 003_add_persona_avatar
"""

from __future__ import annotations

from alembic import op

revision = "004_add_message_images"
down_revision = "003_add_persona_avatar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS images JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS images")
