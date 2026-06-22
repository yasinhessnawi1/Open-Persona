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
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
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
from sqlalchemy.dialects.postgresql import JSONB, REAL, TSVECTOR


def _json() -> JSON:
    """A dialect-neutral JSON column type (Spec 33, D-33-X-json-variant).

    Emits ``JSONB`` on PostgreSQL (cloud — byte-identical to the pre-Spec-33
    DDL, so no migration) and the generic ``JSON`` type on SQLite (community).
    A fresh instance per column keeps each ``Table`` definition independent.
    """
    return JSON().with_variant(JSONB(), "postgresql")


__all__ = [
    "EMBEDDING_DIM",
    "STORE_KINDS",
    "audit_log",
    "conversations",
    "credit_transactions",
    "credits",
    "graph_edges",
    "graph_entities",
    "graph_node_entities",
    "graph_nodes",
    "jobs",
    "jobs_archive",
    "memory_chunks",
    "messages",
    "metadata",
    "persona_mcp_assignments",
    "personas",
    "rate_limit_buckets",
    "runs",
    "turn_logs",
    "user_mcp_servers",
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
    # Spec 21 T09 (D-21-7): tri-state auto-dispatch consent. NULL = never asked /
    # revoked-to-ask, TRUE = granted, FALSE = explicitly declined (stable, never
    # auto-re-prompts — D-21-17). Added by migration 008; nullable so existing
    # rows are unaffected. consent_updated_at stamps the last transition (each
    # transition also emits an AuditEvent at the route).
    Column("consent_to_auto_dispatch", Boolean),
    Column("consent_updated_at", DateTime(timezone=True)),
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
    Column("tool_calls", _json()),
    # Connector passthrough (spec 08, D-08-3, migration 002). Nullable: the web
    # UI sends no channel. The API stores it opaquely and never branches on
    # `platform` — all connector logic is the future spec 12's.
    Column("channel", _json()),
    # Spec 13 D-13-X-now option (c) / migration 004: per-message image refs as
    # JSONB. Each entry is ``{"workspace_path": str, "media_type": str}``. The
    # row holds REFERENCES only — image bytes live exactly once under the Spec
    # 03 workspace (D-13-4). Nullable: text-only messages and every assistant
    # message persist with images = NULL (byte-for-byte unchanged for the
    # text-only path).
    Column("images", _json()),
    # Spec 35 D-35-2 / migration 010: the routing tier (small/mid/frontier) the
    # router chose for this assistant turn, persisted so the per-message tier
    # chip survives a page reload (the live `done` event only covers the
    # just-streamed turn). Nullable: user/system/tool rows and every message
    # written before this migration carry NULL → the chip simply does not render
    # (clean degrade, D-35-2), never a wrong tier.
    Column("tier_used", Text),
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
    Column("steps", _json(), nullable=False, server_default=text("'[]'")),
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

# Spec A0 (durable execution) — the Postgres-backed job queue.
#
# ``jobs`` is the hot queue table: a worker claims with ``SELECT … FOR UPDATE
# SKIP LOCKED`` (the partial ``idx_jobs_claim`` index serves the claim predicate),
# writes a lease, and heartbeats while running; an expired lease returns the job
# to claimable (crash-resume by construction). Hygiene against the known
# Postgres-as-queue slow-death (D-A0-4): ``fillfactor=80`` so status updates stay
# HOT (no index churn), aggressive per-table autovacuum, and a small partial-index
# set tuned to the only two hot predicates (claim + lease-reclaim). ``state``
# mirrors the persona-core ``JobState`` machine; ``idempotency_key`` is UNIQUE so a
# duplicate enqueue is a no-op (``ON CONFLICT (idempotency_key) DO NOTHING``).
# Owner-scoped + RLS like every tenant table — the worker runs as the
# ``persona_app`` non-superuser role (RLS policy created in migration 011).
jobs = Table(
    "jobs",
    metadata,
    Column("id", Text, primary_key=True, server_default=_uuid_pk),
    Column("type", Text, nullable=False),
    Column("owner_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("payload", _json(), nullable=False, server_default=text("'{}'")),
    Column("idempotency_key", Text, nullable=False),
    Column("state", Text, nullable=False, server_default=text("'queued'")),
    Column("priority", Integer, nullable=False, server_default=text("0")),
    Column("attempt", Integer, nullable=False, server_default=text("0")),
    Column("max_attempts", Integer, nullable=False, server_default=text("5")),
    Column("scheduled_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("locked_by", Text),
    Column("last_error", Text),
    CheckConstraint(
        "state IN ('queued', 'claimed', 'running', 'succeeded', 'failed', 'dead')",
        name="jobs_state_check",
    ),
    # Duplicate enqueues dedup on this key (operation+intent scoped — D-A0-X-...).
    # Scoped per OWNER, not globally: a global key namespace would let one tenant
    # pre-register another tenant's key and silently suppress their enqueue
    # (cross-tenant DoS — security review T4). Owner-scoping also matches the RLS
    # WITH CHECK semantics: a key means "this operation for this owner".
    UniqueConstraint("owner_id", "idempotency_key", name="uq_jobs_owner_idempotency_key"),
    # The claim query: WHERE state='queued' AND scheduled_at<=now()
    #                  ORDER BY priority DESC, scheduled_at  (FOR UPDATE SKIP LOCKED).
    Index(
        "idx_jobs_claim",
        text("priority DESC"),
        text("scheduled_at"),
        postgresql_where=text("state = 'queued'"),
    ),
    # The rescuer sweep: reclaim leases that lapsed under a dead/draining worker.
    Index(
        "idx_jobs_lease_expiry",
        "lease_expires_at",
        postgresql_where=text("state IN ('claimed', 'running')"),
    ),
    # RLS predicate filters on owner_id.
    Index("idx_jobs_owner", "owner_id"),
    # Claim-time fairness (T7): the per-user + global in-flight counts scan only
    # claimed/running rows; this partial index serves both (by owner, and total).
    Index(
        "idx_jobs_inflight_by_owner",
        "owner_id",
        postgresql_where=text("state IN ('claimed', 'running')"),
    ),
)
# D-A0-4 hygiene (HOT updates via fillfactor + aggressive per-table autovacuum)
# is applied as storage reloptions in migration 011 — ``ALTER TABLE jobs SET
# (...)`` — not here, since SQLAlchemy's ``Table`` has no portable reloptions
# kwarg. Tunable via ALTER once the soak test (A0-R-3) measures the real churn.

# ``jobs_archive`` is the cold table terminal jobs age out into (the cleaner sweep,
# D-A0-4): keeps the hot table's working set tiny while A3/A6 still read job
# history. Same shape as ``jobs`` plus ``archived_at``; no claim/lease indexes
# (never claimed) and no idempotency UNIQUE (the same key may recur across time —
# the live dedup lives on the hot table). Terminal-only by CHECK. Owner-scoped +
# RLS (it holds tenant data; migration 011 creates the policy).
jobs_archive = Table(
    "jobs_archive",
    metadata,
    Column("id", Text, primary_key=True),
    Column("type", Text, nullable=False),
    Column("owner_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("payload", _json(), nullable=False, server_default=text("'{}'")),
    Column("idempotency_key", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("priority", Integer, nullable=False, server_default=text("0")),
    Column("attempt", Integer, nullable=False, server_default=text("0")),
    Column("max_attempts", Integer, nullable=False, server_default=text("5")),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("locked_by", Text),
    Column("last_error", Text),
    Column("archived_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "state IN ('succeeded', 'failed', 'dead')",
        name="jobs_archive_state_check",
    ),
    Index("idx_jobs_archive_owner", "owner_id"),
    # Retention sweeps delete by age.
    Index("idx_jobs_archive_archived_at", "archived_at"),
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
    Column("metadata", _json(), nullable=False, server_default=text("'{}'")),
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
    Column("metadata", _json(), nullable=False, server_default=text("'{}'")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("idx_audit_user", "user_id"),
    Index("idx_audit_created", "created_at"),
)

# Spec 30 (D-30-3) — bring-your-own MCP servers. User-scoped (reusable across the
# user's personas), RLS-keyed to owner_id. The user supplies an outbound URL the
# runtime connects to → SSRF-sensitive (validated at the route + at connect, T08).
# Credentials are encrypted at rest (Fernet, T07) in ``credentials_encrypted`` —
# NEVER stored or logged in plaintext; ``auth_method`` ∈ {none, bearer, header}.
# ``discovered_tools`` caches the eager-on-add discovery (D-30-5) for the UI.
user_mcp_servers = Table(
    "user_mcp_servers",
    metadata,
    Column("id", Text, primary_key=True, server_default=_uuid_pk),
    Column("owner_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("name", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("auth_method", Text, nullable=False, server_default=text("'none'")),
    # Fernet token (T07). NULL when auth_method = 'none'. Never plaintext, never logged.
    Column("credentials_encrypted", Text),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    # Cached tool list from eager discovery on add/test (D-30-5); refreshed lazily.
    Column("discovered_tools", _json()),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # No duplicate server names per user (the name keys the mcp:<name>: prefix).
    UniqueConstraint("owner_id", "name", name="uq_user_mcp_servers_owner_name"),
    Index("idx_user_mcp_servers_owner", "owner_id"),
)

# Spec 30 (D-30-6) — persona ↔ BYO-server assignment. Many-personas-to-one-server
# so the YAML stays credential-free (no raw URLs in the versioned persona). The
# toolbox resolves a persona's assignments to ``mcp:<server>:<tool>`` at load (T10).
persona_mcp_assignments = Table(
    "persona_mcp_assignments",
    metadata,
    Column("persona_id", Text, ForeignKey("personas.id", ondelete="CASCADE"), nullable=False),
    Column(
        "server_id",
        Text,
        ForeignKey("user_mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("persona_id", "server_id", name="pk_persona_mcp_assignments"),
    Index("idx_persona_mcp_assignments_server", "server_id"),
)

# ---------------------------------------------------------------------------
# Spec K0 — the user-scoped knowledge graph (direction 3).
#
# Three Postgres-only tables (pgvector ``Vector`` + ``tsvector``); user-scoped via
# direct ``owner_id`` (NOT the persona FK-chain — the graph is per *user*). Their
# RLS lives ENTIRELY in migration ``011_knowledge_graph`` (mirroring migration
# 009 — so ``001``'s downgrade never ALTERs a later table) and is therefore NOT
# in ``db.rls._POLICIES``. They are excluded from the community SQLite build
# (``db.community._CLOUD_ONLY_TABLES``) — vectors/FTS are cloud-only, like
# ``memory_chunks``. The persona-core transport (``persona.graph._schema``)
# defines its own view of these; a contract test asserts the two agree (D-K0-3).
# ---------------------------------------------------------------------------

graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("id", Text, primary_key=True),
    # The turbovec uint64 index key (D-K0-3): collision-free + monotonic.
    Column("surrogate", BigInteger, Identity(always=True), nullable=False, unique=True),
    Column("owner_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("node_kind", Text, nullable=False),
    Column("concept_name", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column("wellbeing_category", Text),
    Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    Column("embedding_model", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
    # The accumulation trail (D-K0-4): a JSONB array of NodeProvenance dumps.
    Column("provenance", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column(
        "fts",
        TSVECTOR,
        Computed("to_tsvector('english', concept_name || ' ' || content)", persisted=True),
    ),
    # Lets graph_edges reference (id, owner_id) so an edge can never cross tenants.
    UniqueConstraint("id", "owner_id", name="uq_graph_nodes_id_owner"),
    Index("ix_graph_nodes_owner", "owner_id"),
    Index(
        "ix_graph_nodes_embedding_hnsw",
        "embedding",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    ),
    Index("ix_graph_nodes_fts", "fts", postgresql_using="gin"),
)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("id", Text, primary_key=True),
    Column("owner_id", Text, nullable=False),
    Column("src_node_id", Text, nullable=False),
    Column("dst_node_id", Text, nullable=False),
    Column("link_type", Text, nullable=False),
    Column("weight", REAL),
    Column("provenance", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "link_type IN ('semantic', 'entity', 'temporal', 'causal')",
        name="graph_edges_link_type_check",
    ),
    # Composite FKs: both endpoints belong to the SAME owner (finding-1
    # defense-in-depth); ON DELETE CASCADE removes a node's edges with it.
    ForeignKeyConstraint(
        ["src_node_id", "owner_id"],
        ["graph_nodes.id", "graph_nodes.owner_id"],
        ondelete="CASCADE",
        name="fk_graph_edges_src_owner",
    ),
    ForeignKeyConstraint(
        ["dst_node_id", "owner_id"],
        ["graph_nodes.id", "graph_nodes.owner_id"],
        ondelete="CASCADE",
        name="fk_graph_edges_dst_owner",
    ),
    Index("ix_graph_edges_src", "owner_id", "src_node_id", "link_type"),
    Index("ix_graph_edges_dst", "owner_id", "dst_node_id", "link_type"),
)

graph_entities = Table(
    "graph_entities",
    metadata,
    Column("id", Text, primary_key=True),
    Column("owner_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("canonical_name", Text, nullable=False),
    Column("aliases", JSONB, nullable=False),
    Column("name_embedding", Vector(EMBEDDING_DIM), nullable=False),
    Column("provenance", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("id", "owner_id", name="uq_graph_entities_id_owner"),
    Index("ix_graph_entities_owner", "owner_id"),
    Index(
        "ix_graph_entities_name_hnsw",
        "name_embedding",
        postgresql_using="hnsw",
        postgresql_ops={"name_embedding": "vector_cosine_ops"},
    ),
)

# node ↔ canonical-entity associations (Spec K0, T6b). Substrate for entity links
# (criterion 2); ENTITY traversal resolves through this table on-the-fly (no
# materialized node↔node entity edges). RLS via direct owner_id (migration 011).
graph_node_entities = Table(
    "graph_node_entities",
    metadata,
    Column("owner_id", Text, nullable=False),
    Column("node_id", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("node_id", "entity_id", name="pk_graph_node_entities"),
    ForeignKeyConstraint(
        ["node_id", "owner_id"],
        ["graph_nodes.id", "graph_nodes.owner_id"],
        ondelete="CASCADE",
        name="fk_gne_node_owner",
    ),
    ForeignKeyConstraint(
        ["entity_id", "owner_id"],
        ["graph_entities.id", "graph_entities.owner_id"],
        ondelete="CASCADE",
        name="fk_gne_entity_owner",
    ),
    Index("ix_gne_entity", "owner_id", "entity_id"),
    Index("ix_gne_node", "owner_id", "node_id"),
)
