# Changelog

All notable changes to Open Persona are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Per-spec entries are added by the close-out phase of each spec.

---

## [Unreleased]

_Nothing here yet._

## [0.2.0] — 2026-05-27

Spec 02 close-out. Model backends and provider abstraction.

### Added
- `persona.backends.ChatBackend` async Protocol with `chat()` (single-shot) + `chat_stream()` (`AsyncIterator[StreamChunk]`). ([`backends/protocol.py`](packages/core/src/persona/backends/protocol.py))
- `OpenAICompatibleBackend` — unified backend for Anthropic (via `anthropic` SDK) and OpenAI / DeepSeek / Groq / Together (via `openai.AsyncOpenAI` with per-provider `base_url`). Native tool calling where the provider supports it; prompt-based JSON-block shim fallback. ([`backends/openai_compat.py`](packages/core/src/persona/backends/openai_compat.py))
- `OllamaBackend` — raw `httpx` to a local Ollama instance at `/api/chat`; lazy client; opt-in native tools (`use_native_tools=True`); explicit `ping()` health check; `aclose()` for lifecycle. ([`backends/ollama.py`](packages/core/src/persona/backends/ollama.py))
- `HFLocalBackend` behind `persona-core[local]` extras — lazy weight load via `asyncio.Lock`-guarded `_ensure_loaded()`; 4-bit NF4 / 8-bit / fp16 quantisation; Gemma-2 system-role fold + eager attention; `generation_config` override; `AsyncTextIteratorStreamer` for async streaming with `_CancellableStoppingCriteria`. ([`backends/hf_local.py`](packages/core/src/persona/backends/hf_local.py))
- Five new domain exceptions: `ProviderError`, `AuthenticationError`, `RateLimitError`, `ModelNotFoundError`, `BackendTimeoutError` — all subclasses of `PersonaError`, carry structured `context` per the engineering standards. ([`backends/errors.py`](packages/core/src/persona/backends/errors.py))
- Prompt-based tool-calling shim (`{"tool": "name", "args": {...}}` JSON blocks) with fail-safe parser (D-02-14). ([`backends/_tool_shim.py`](packages/core/src/persona/backends/_tool_shim.py))
- `BackendConfig` (Pydantic Settings, `PERSONA_*` env-only) with `from_env(prefix=...)` for tier-specific overrides (used by spec 05). ([`backends/config.py`](packages/core/src/persona/backends/config.py))
- `load_backend(BackendConfig)` factory + `persona.backends` package re-exports. ([`backends/__init__.py`](packages/core/src/persona/backends/__init__.py), [`backends/_factory.py`](packages/core/src/persona/backends/_factory.py))
- Response types: `ChatResponse`, `StreamChunk`, `TokenUsage`, `ToolSpec`, `ToolCallDelta` — Pydantic v2 frozen + `extra="forbid"` (D-02-2). `tool_spec_from_tool()` helper bridges spec-01's `Tool` Protocol. ([`backends/types.py`](packages/core/src/persona/backends/types.py))
- CLI: `persona chat` now wires through `load_backend(BackendConfig())` and streams via `chat_stream()`; `EchoBackend` placeholder deleted (D-02-12). ([`cli/chat_cmd.py`](packages/core/src/persona/cli/chat_cmd.py))
- Test helper `MockChatBackend` in `tests/_mock_backend.py` for CLI / integration tests (replaces deleted `_echo.py`).
- Contract test suite ([`tests/contract/test_chat_backend_contract.py`](packages/core/tests/contract/test_chat_backend_contract.py)) — 26 parametrised tests across 4 backend variants verifying Protocol compliance, chat shape, streaming, fail-fast auth, and tool-call round-trip.

### Changed
- `packages/core/pyproject.toml` — added `anthropic>=0.30,<1` and `openai>=1.30,<2` as core dependencies; `httpx>=0.27,<1` (parked under D-01-11) now live.
- `.env.example` — added `PERSONA_PROVIDER`, per-provider key vars, `PERSONA_BASE_URL`, `PERSONA_REQUEST_TIMEOUT_S`, `PERSONA_DOTENV_LOAD`, and HF local vars.
- `packages/core/SPEC.md` — model backends subsection added.

### Removed
- `packages/core/src/persona/cli/_echo.py` (deleted per D-02-12). Production no longer ships a fake backend; tests inject their own.

### Tests
- 414 unit (was 210; +204 new in `tests/unit/backends/`) + 28 integration + 26 contract = **468 total green**.
- New file: `tests/contract/test_chat_backend_contract.py` runs the same assertions against every backend variant.
- All checks: `ruff check`, `ruff format --check`, `mypy --strict packages/core/src` clean (47 source files).

### Documentation
- `docs/specs/spec_02/{spec_02_backends.md, tasks.md, tasks.yaml, research.md, decisions.md, state.md, handover.md, README.md, closeout.md}` — full lifecycle of Spec 02 captured.
- D-02-1..D-02-18 added to root [`docs/DECISIONS.md`](docs/DECISIONS.md).

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
