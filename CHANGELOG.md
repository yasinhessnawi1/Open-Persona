# Changelog

All notable changes to Open Persona are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Per-spec entries are added by the close-out phase of each spec.

---

## [Unreleased]

_Nothing here yet._

## [0.1.0] — 2026-05-27

First spec close-out. Foundation of `persona-core`.

### Added
- v1.0 persona YAML schema (Pydantic v2, frozen, `extra="forbid"`) covering identity, self-facts, worldview, episodic, routing, embedding, tools, skills. ([`schema/persona.py`](packages/core/src/persona/schema/persona.py))
- `PersonaChunk` with deterministic SHA-256 `content_hash`, tz-aware UTC datetimes, and `ChunkProvenance` for the version chain. ([`schema/chunks.py`](packages/core/src/persona/schema/chunks.py))
- Three-source persona update model (`system` / `user` / `persona_self`) with per-store policy table. Versioned append-only updates with `history` and `rollback`. ([`stores/policy.py`](packages/core/src/persona/stores/policy.py), [`stores/versioning.py`](packages/core/src/persona/stores/versioning.py))
- `MemoryStore` protocol + four concrete typed stores: `IdentityStore`, `SelfFactsStore`, `WorldviewStore`, `EpisodicStore`. Episodic decay is query-time exponential (`tau=24h` default).
- `ChromaMemoryStore` transport with deterministic per-`(persona, store_kind)` collection naming, cosine-distance HNSW, SQLite query-batch cap, and provenance serialised into Chroma metadata.
- `PersonaRegistry` — load YAML, validate, index author-time chunks; idempotent re-load.
- `ConversationHistoryManager` — summarise-and-compact (`compact_every=10`, `keep_recent=5`). Summariser injected.
- Per-component logging via `loguru` (`persona.logging.get_logger`), idempotent sink configuration (D-01-7).
- JSONL audit log behind an `AuditLogger` Protocol; every store mutation emits exactly one `AuditEvent`. (`MemoryAuditLogger` for tests.)
- Typer CLI: `persona init`, `persona validate`, `persona chat` (placeholder `EchoBackend`), `persona audit`, `persona run` (stub for spec 06).
- `py.typed` marker shipped in the wheel; structured-context domain exceptions; CHANGELOG; .editorconfig; pre-commit hooks (ruff, ruff-format, mypy --strict, pytest --collect-only).

### Infrastructure
- Root `pyproject.toml` declares workspace members as root dependencies so a plain `uv sync` installs the whole monorepo.
- `[tool.uv.sources]` blocks in `packages/runtime/pyproject.toml` and `packages/api/pyproject.toml` (required by uv).
- Root `conftest.py` prepends each workspace `src/` to `sys.path` to work around CPython 3.13's hidden-`_editable_impl_` `.pth` skip.

### Tests
- 210 unit + 28 integration tests across 11 test files. 10 valid + 10 invalid persona YAML fixtures. Pure-function policy table tested in isolation; concrete stores tested against real ChromaDB.

### Documentation
- `docs/specs/spec_01/{spec_01_core.md, tasks.md, tasks.yaml, research.md, decisions.md, state.md, handover.md, README.md, closeout.md}` — full lifecycle of Spec 01 captured.
