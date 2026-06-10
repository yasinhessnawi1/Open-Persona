"""Canonical SQLAlchemy Core schema for the hosted service (spec 07, T03).

This module is the single source of truth for the production schema's *shape*.
The Alembic ``001_initial`` migration (T04) creates these tables (plus RLS, T06)
via explicit SQL; this ``MetaData`` is wired as Alembic's ``target_metadata`` so
autogenerate stays sane. The ``persona-core`` Postgres transport
(``stores/postgres.py``) defines its own minimal view of ``memory_chunks`` —
core cannot import this api-only module — and a contract test (T07) asserts the
two agree.

Decisions in force here:

- **D-07-2:** SQLAlchemy Core (no ORM). Typed ``Table`` definitions, queried
  with parameterised Core expressions.
- **D-07-4:** ``memory_chunks`` *promotes* the versioning/provenance fields
  (``logical_id``/``version``/``superseded_by`` + ``content_hash`` + the
  ``ChunkProvenance`` fields) to indexed columns; user metadata lives in the
  ``metadata`` JSONB column; identity chunks store NULL provenance. ``decay_t0``
  is dropped — decay anchors on ``created_at`` at query time (D-01-4).
- **D-07-4 / S07-3:** HNSW index with ``vector_cosine_ops`` on ``embedding``;
  a partial index on the current-heads predicate (``superseded_by IS NULL``).
- **Embedding dim:** ``vector(384)`` (bge-small-en-v1.5). v0.1 single-embedder.
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

__all__ = [
    "EMBEDDING_DIM",
    "STORE_KINDS",
    "audit_log",
    "conversations",
    "credit_transactions",
    "credits",
    "memory_chunks",
    "messages",
    "metadata",
    "personas",
    "rate_limit_buckets",
    "runs",
    "turn_logs",
    "users",
]

# bge-small-en-v1.5 (architecture §4.3). The column is hard-coded to this dim;
# a chunk whose embedding length differs is rejected at write (steer #7).
EMBEDDING_DIM = 384

STORE_KINDS = ("identity", "self_facts", "worldview", "episodic")

metadata = MetaData()

_uuid_pk = text("gen_random_uuid()::text")


users = Table(
    "users",
    metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, unique=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

personas = Table(
    "personas",
    metadata,
    Column("id", Text, primary_key=True, server_default=_uuid_pk),
    Column("owner_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("yaml", Text, nullable=False),
    Column("schema_version", Text, nullable=False, server_default=text("'1.0'")),
    # Visual identity for the persona list / chat header (nullable: user-uploaded
    # or auto-generated-from-initials by the frontend). Added by migration 003,
    # pre-spec-09 patch. Not part of the persona YAML schema — a presentation
    # field owned by the API row.
    Column("avatar_url", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Lets conversations/runs reference (persona_id, owner_id) as a composite FK
    # so a row can never attach to another tenant's persona (defence-in-depth
    # beyond RLS — see spec-07 closeout / security review finding 1).
    UniqueConstraint("id", "owner_id", name="uq_personas_id_owner"),
    Index("idx_personas_owner", "owner_id"),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", Text, primary_key=True, server_default=_uuid_pk),
    Column("owner_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("persona_id", Text, nullable=False),
    Column("title", Text, nullable=False, server_default=text("''")),
    Column("compacted_summary", Text, nullable=False, server_default=text("''")),
    Column("compacted_up_to", Integer, nullable=False, server_default=text("0")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Composite FK: the persona must belong to the SAME owner (the DB rejects a
    # conversation attached to another tenant's persona — security finding 1).
    ForeignKeyConstraint(
        ["persona_id", "owner_id"],
        ["personas.id", "personas.owner_id"],
        ondelete="CASCADE",
        name="fk_conversations_persona_owner",
    ),
    Index("idx_conversations_owner", "owner_id"),
    Index("idx_conversations_persona", "persona_id"),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Text, primary_key=True, server_default=_uuid_pk),
    Column(
        "conversation_id",
        Text,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("role", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("tool_calls", JSONB),
    # Connector passthrough (spec 08, D-08-3, migration 002). Nullable: the web
    # UI sends no channel. The API stores it opaquely and never branches on
    # `platform` — all connector logic is the future spec 12's.
    Column("channel", JSONB),
    # Spec 13 D-13-X-now option (c) / migration 004: per-message image refs as
    # JSONB. Each entry is ``{"workspace_path": str, "media_type": str}``. The
    # row holds REFERENCES only — image bytes live exactly once under the Spec
    # 03 workspace (D-13-4). Nullable: text-only messages and every assistant
    # message persist with images = NULL (byte-for-byte unchanged for the
    # text-only path).
    Column("images", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("role IN ('system', 'user', 'assistant', 'tool')", name="messages_role_check"),
    Index("idx_messages_conversation", "conversation_id"),
    Index("idx_messages_created", "conversation_id", "created_at"),
)

runs = Table(
    "runs",
    metadata,
    Column("id", Text, primary_key=True, server_default=_uuid_pk),
    Column("owner_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("persona_id", Text, nullable=False),
    Column("task", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'running'")),
    Column("steps", JSONB, nullable=False, server_default=text("'[]'")),
    Column("output", Text),
    Column("error", Text),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint(
        "status IN ('running', 'awaiting_user', 'completed', 'cancelled', "
        "'max_steps_reached', 'error')",
        name="runs_status_check",
    ),
    # Composite FK: a run's persona must belong to the same owner (finding 1).
    ForeignKeyConstraint(
        ["persona_id", "owner_id"],
        ["personas.id", "personas.owner_id"],
        ondelete="CASCADE",
        name="fk_runs_persona_owner",
    ),
    Index("idx_runs_owner", "owner_id"),
)

# D-07-4: memory_chunks promotes provenance/versioning to indexed columns.
memory_chunks = Table(
    "memory_chunks",
    metadata,
    Column("id", Text, primary_key=True),
    # persona_id has discriminated semantics gated by ``kind`` (v0.1.1, migration 007):
    # rows with kind in {identity,self_facts,worldview,episodic} carry a personas.id;
    # rows with kind='document' carry a conversations.id (per migration 005's RLS aux
    # policy). PostgreSQL cannot express conditional FKs natively; the integrity
    # guarantee splits across the kind CHECK + RLS policy. Migration 007 drops the
    # FK to personas.id; here we omit the ForeignKey so create_all() matches the
    # post-migration shape.
    Column("persona_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    Column(
        "embedding_model",
        Text,
        nullable=False,
        server_default=text("'bge-small-en-v1.5'"),
    ),
    Column("content_hash", Text, nullable=False),
    # User-supplied PersonaChunk.metadata (string-valued) — NOT provenance.
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'")),
    # Promoted ChunkProvenance (NULL for identity chunks, which never version).
    Column("logical_id", Text),
    Column("version", Integer),
    Column("superseded_by", Text),
    Column("prov_source", Text),
    Column("written_at", DateTime(timezone=True)),
    Column("written_by", Text),
    Column("reason", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Spec 19 D-19-X-memory-chunks-kind-check-migration (chain entry 23):
    # ``'document'`` is the fifth accepted kind so the DocumentStore path
    # (migration 005 RLS aux policy) survives the CHECK. Migration 006 is the
    # migration of record for existing deployments; this canonical declaration
    # is the source of truth for fresh DBs via ``001_initial`` create_all.
    CheckConstraint(
        "kind IN ('identity', 'self_facts', 'worldview', 'episodic', 'document')",
        name="memory_chunks_kind_check",
    ),
    CheckConstraint(
        "prov_source IS NULL OR prov_source IN ('system', 'user', 'persona_self')",
        name="memory_chunks_prov_source_check",
    ),
    Index("idx_memory_persona_kind", "persona_id", "kind"),
    Index("idx_memory_persona_kind_logical", "persona_id", "kind", "logical_id"),
    # Partial index: the hot current-heads view (query/get_all default).
    Index(
        "idx_memory_current_heads",
        "persona_id",
        "kind",
        postgresql_where=text("superseded_by IS NULL"),
    ),
    # HNSW approximate-NN over cosine distance (S07-3).
    Index(
        "idx_memory_embedding",
        "embedding",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    ),
)

turn_logs = Table(
    "turn_logs",
    metadata,
    Column("id", Text, primary_key=True, server_default=_uuid_pk),
    Column(
        "conversation_id",
        Text,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("turn_index", Integer, nullable=False),
    Column("tier_used", Text, nullable=False),
    Column("model_name", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("prompt_tokens", Integer, nullable=False),
    Column("completion_tokens", Integer, nullable=False),
    Column("latency_ms", Float, nullable=False),
    Column("cost_cents", Float, nullable=False, server_default=text("0")),
    Column("tool_calls", Integer, nullable=False, server_default=text("0")),
    Column("skill_used", Text),
    Column(
        "history_compacted",
        Boolean,
        nullable=False,
        server_default=text("FALSE"),
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("idx_turn_logs_conversation", "conversation_id"),
)

rate_limit_buckets = Table(
    "rate_limit_buckets",
    metadata,
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("endpoint", Text, nullable=False),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("request_count", Integer, nullable=False, server_default=text("0")),
    PrimaryKeyConstraint("user_id", "endpoint", "window_start"),
)

credits = Table(  # noqa: A001 — schema table name (spec §5), not the stdlib `credits`
    "credits",
    metadata,
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("balance", Integer, nullable=False, server_default=text("100000")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

credit_transactions = Table(
    "credit_transactions",
    metadata,
    Column("id", Text, primary_key=True, server_default=_uuid_pk),
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("delta", Integer, nullable=False),
    Column("reason", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("idx_credit_tx_user", "user_id"),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Text, primary_key=True, server_default=_uuid_pk),
    Column("user_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("target", Text, nullable=False),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("idx_audit_user", "user_id"),
    Index("idx_audit_created", "created_at"),
)
