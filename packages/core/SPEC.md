# persona-core — Package Spec

> One-page reference for the `persona-core` package. Authoritative source for the package's surface area, dependencies, and test strategy. Pinned per architecture §8 week 0 and engineering standards §6.
>
> The full spec lives in [`/docs/specs/spec_01/spec_01_core.md`](../../docs/specs/spec_01/spec_01_core.md). The repo-wide architecture lives in [`/docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

**Version:** 0.1.0 (Spec 01)
**License:** Apache 2.0

---

## What this package is

The open-source Python library that ships as `pip install persona-core`. The foundation for the Persona platform. Every other package (`persona-runtime`, `persona-api`, `persona-web`) imports from this one; this one imports from nothing inside Open Persona.

Contains: persona YAML schema (v1.0), four typed memory stores behind a `MemoryStore` protocol, ChromaDB-backed implementation, conversation history manager (summarise-and-compact), CLI, per-component logging (loguru), JSONL audit log behind an `AuditLogger` protocol.

## Public API surface

```
persona
├── schema/
│   ├── persona.py     Persona, PersonaIdentity, SelfFact, WorldviewClaim, EpisodicEntry,
│   │                  RoutingConfig, EmbeddingConfig
│   ├── conversation.py ConversationMessage, Conversation, ConversationHistory
│   ├── chunks.py      PersonaChunk, ChunkProvenance, WriteSource, make_chunk_id
│   ├── tools.py       Tool (Protocol), ToolCall, ToolResult
│   └── skills.py      SkillSpec
├── stores/
│   ├── protocol.py    MemoryStore (Protocol)
│   ├── identity.py    IdentityStore
│   ├── self_facts.py  SelfFactsStore
│   ├── worldview.py   WorldviewStore
│   ├── episodic.py    EpisodicStore
│   ├── chroma.py      ChromaMemoryStore
│   ├── versioning.py  compute_next_version, link_supersedes, validate_chain
│   └── errors.py      RuntimeWriteForbiddenError, PersonaSelfWriteForbiddenError, ...
├── audit.py           AuditAction, AuditEvent, AuditLogger (Protocol),
│                      JSONLAuditLogger, MemoryAuditLogger
├── registry.py        PersonaRegistry
├── history.py         ConversationHistoryManager
├── logging.py         get_logger
├── config.py          PersonaCoreConfig
├── errors.py          PersonaError, SchemaVersionMismatchError, PersonaNotFoundError, ...
└── cli/               persona init|validate|chat|audit|run (run is a stub until spec 06)
```

Everything else under `persona/` is private (`_`-prefixed or implementation detail).

## Public guarantees

- Frozen Pydantic v2 models with `extra="forbid"` everywhere data crosses a boundary.
- Tz-aware UTC datetimes — naive datetimes raise at construction.
- Deterministic chunk IDs: `{persona_id}::{store_kind}::{index:04d}`.
- Every `PersonaChunk` carries a SHA-256 `content_hash` for tamper detection.
- Three-source per-write policy: `WriteSource` ∈ `{system, user, persona_self}`. Per-store policy table enforces source × force-flag rules.
- Versioned append-only stores (self_facts, worldview, episodic). Identity is immutable at runtime.
- Every successful store mutation emits exactly one `AuditEvent`.
- Per-component logging via `get_logger(component)`. Idempotent sink configuration — safe to import multiple times.

See [`docs/specs/spec_01/spec_01_core.md`](../../docs/specs/spec_01/spec_01_core.md) §5–§7 for the full semantic spec.

## Dependencies

```
pydantic>=2.7,<3
pydantic-settings>=2.3,<3
chromadb>=1.0,<2
sentence-transformers>=3.0,<4
typer>=0.12,<1
pyyaml>=6.0,<7
loguru>=0.7,<1
httpx>=0.27,<1       # parked; spec 02 (model backends)
tiktoken>=0.7,<1     # parked; spec 05 (prompt builder)
```

Optional extras:
- `[local]` — torch, transformers, bitsandbytes, accelerate (for Gemma/Llama local backends in spec 02).
- `[postgres]` — asyncpg, sqlalchemy[asyncio], pgvector (for spec 07's PostgresPGVectorStore).

Dependency rationale lives in [`docs/specs/spec_01/research.md`](../../docs/specs/spec_01/research.md) §1.

## Test strategy

- **`tests/unit/`** — pure unit tests, all external deps mocked. Run on every push, under 60 seconds.
- **`tests/integration/`** — real ChromaDB persistence round-trips. Marked `@pytest.mark.integration`. Skipped by default.
- **`tests/contract/`** — verifies every concrete `MemoryStore` honours the protocol contract.

Coverage target: every public class and function has at least one test.

## What this package does *not* contain

- Model backends (spec 02).
- Tool implementations (spec 03).
- Skill implementations (spec 04).
- Conversation loop, router, agentic loop (spec 05/06).
- `PostgresPGVectorStore` (spec 07 — same protocol, different backend).
- HTTP API or web UI.

## Versioning

Spec 01 ships as `persona-core 0.1.0`. Subsequent specs that add to the public surface bump the minor version. Breaking changes bump the major.
