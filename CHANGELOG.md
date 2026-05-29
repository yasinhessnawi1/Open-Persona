# Changelog

All notable changes to Open Persona are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Per-spec entries are added by the close-out phase of each spec.

---

## [Unreleased]

### Added (pre-spec-09 API polish)
- **Auto-title on first message** — the first turn of a conversation generates a short title from the user's message via the small tier (best-effort: a summariser failure keeps the default title and never breaks the turn). Wired as an injectable `title_builder` on `app.state` (the runtime factory builds it from the small tier). ([`chat_service.py`](packages/api/src/persona_api/services/chat_service.py), [`runtime_factory.py`](packages/api/src/persona_api/services/runtime_factory.py))
- **`personas.avatar_url`** — a nullable presentation field (not part of the persona YAML schema) for the persona-list / chat-header visual identity. Accepted on create/PATCH (PATCH leaves it untouched when omitted), surfaced on `PersonaSummary`/`PersonaDetail`. Migration `003_add_persona_avatar`. ([`003_add_persona_avatar.py`](packages/api/alembic/versions/003_add_persona_avatar.py))
- **`DELETE /v1/conversations/:id`** — deletes a conversation and cascades to its messages + turn_logs (FK `ON DELETE CASCADE`); RLS-scoped (404 cross-tenant). ([`conversations.py`](packages/api/src/persona_api/routes/conversations.py))

### Fixed (closes a Spec-08 gap Spec 09 found)
- **Chat SSE now emits `tool_calling` + `tool_result` events and a real `done.tier`.** The shipped chat stream emitted only `chunk` + `done` and hardcoded `tier: "frontier"`, so the web app couldn't render tool-call cards or a real per-turn tier badge (Spec-09 acceptance #2). `ConversationLoop.turn` gained an optional `on_event` callback (mirroring `AgenticLoop.run`) that surfaces tool calls/results + the router's tier using the **same `RunEvent` shapes as the run-viewer stream** (one event vocabulary for both); `chat_service` maps them to ordered SSE frames before `done`, with `done.tier` = the real choice. `tool_result` is `is_error`+`content` (no `error` field, D-03-3). Added a run-level `RunEvent.tier` constructor. No change to `turn`'s yield contract (the param defaults to `None`). ([`loop.py`](packages/runtime/src/persona_runtime/loop.py), [`events.py`](packages/runtime/src/persona_runtime/agentic/events.py), [`chat_service.py`](packages/api/src/persona_api/services/chat_service.py))

## [0.8.0] — 2026-05-28

Spec 08 close-out. The **hosted FastAPI service** (`persona-api`) — the composition root where the sync stores (07), the sync runtime loops (05–06), the toolbox (03), and the backends (02) wire together inside async FastAPI. Auth + RLS, persona CRUD + LLM authoring, SSE streaming chat, background agentic runs, rate limiting, a credits stub, observability, and an auto-generated OpenAPI surface. The headline is the **structural RLS contract** (D-08-1): a per-request engine pool listener sets `app.current_user_id` on every connection from a request-scoped contextvar, so tenant isolation is a property of the engine — not a per-route discipline that could be forgotten. A security-reviewer pass found and the fix closed a HIGH JWT algorithm-confusion bug; the full tenant-boundary review is otherwise clean.

### Added
- `persona_api.app` — the `create_app()` factory + lifespan composition root. The lifespan owns the RLS engine, the embedder, the rate limiter, the agentic-run registry, and the app-scoped `TierRegistry`; on shutdown it calls `await tier_registry.aclose()` + `await client.disconnect()` per MCP client (D-05-4). ([`app.py`](packages/api/src/persona_api/app.py), [`config.py`](packages/api/src/persona_api/config.py))
- `persona_api.middleware.rls_context` — **the structural RLS mechanism (D-08-1).** `make_rls_engine` attaches `checkout`/`checkin` pool listeners that `set_config('app.current_user_id', <uid or ''>, false)` from a request-scoped `contextvar` and reset it — so every connection a request touches (route queries AND the runtime store's own `engine.begin()`) is tenant-scoped, and an absent uid fails closed. Settled by an adversarial spike (Phase 3). ([`rls_context.py`](packages/api/src/persona_api/middleware/rls_context.py))
- `persona_api.auth` — the injectable `verify_token` seam (D-08-4). `python-jose` JWT verification with the key bound to the token's algorithm family (HMAC→secret, RSA/EC→public key) to prevent algorithm-confusion; fail-fast on key/alg mismatch. `get_current_user` sets the RLS contextvar for the request. Tests override the seam with a fake JWT. ([`deps.py`](packages/api/src/persona_api/auth/deps.py))
- `persona_api.schemas` — frozen Pydantic request/response models. The approved connector-agnostic change (D-08-3): an optional nullable `ChannelContext` on the message request + `format_hints` on the SSE `done` event — opaque passthrough the API never branches on. ([`requests.py`](packages/api/src/persona_api/schemas/requests.py), [`responses.py`](packages/api/src/persona_api/schemas/responses.py))
- Routes: personas CRUD + LLM authoring; conversations + SSE chat (KEYSTONE 1); agentic runs start/events/respond/cancel (KEYSTONE 2); `/me/credits` + `/me/usage`; `/v1/tools` + `/v1/skills`; `/healthz`. ([`routes/`](packages/api/src/persona_api/routes/))
- Services: `persona_service` (CRUD + memory-store population on create, D-08-8), `chat_service` (SSE chat, persist-after-final, channel passthrough), `run_service` + `background/run_worker` (in-process `asyncio.Task` runs, per-run event-bus queue, blocking `user_respond`, cancel, per-step persist — D-08-5), `authoring_service`, `credits_service` (deduct-after-success, D-08-6), `audit_service`, `catalog_service`, `runtime_factory` (builds the real loops per request). ([`services/`](packages/api/src/persona_api/services/))
- `persona_api.middleware.rate_limit` — per-user/per-endpoint/per-minute limiter (§6) with in-memory + Postgres stores; `X-RateLimit-*` headers. ([`rate_limit.py`](packages/api/src/persona_api/middleware/rate_limit.py))
- `PostgresTurnLogWriter` (D-08-7) — the spec-05 `TurnLogWriter` Protocol against `turn_logs`, injected into the conversation loop. ([`turn_log_writer.py`](packages/api/src/persona_api/services/turn_log_writer.py))
- Alembic `002_add_message_channel` — the first incremental migration: a nullable `messages.channel` JSONB column (D-08-3). ([`002_add_message_channel.py`](packages/api/alembic/versions/002_add_message_channel.py))

### Changed
- `persona.registry.PersonaRegistry` — added a public `load_persona(persona)` (the string/object-input sibling of `load(path)`) so the API indexes a request-body persona without reaching into private internals; `load(path)` delegates to it. ([`registry.py`](packages/core/src/persona/registry.py))
- `persona.tools.build_default_toolbox` — added an `extra_tools=` parameter so the composition root folds in the `use_skill` tool (D-04-10: not auto-registered) without touching the Toolbox's private state. ([`_factory.py`](packages/core/src/persona/tools/_factory.py))
- `persona-runtime` ships a `py.typed` marker so `persona-api` (standard mypy) can import its types. ([`py.typed`](packages/runtime/src/persona_runtime/py.typed))

### Security
- **HIGH (fixed):** the JWT verifier chose the verification key independently of the algorithm — an RS256 deployment that left `jwt_algorithms` at its HS256 default could be tricked into verifying an HS256 token forged with the (public) RSA key as the HMAC secret (algorithm-confusion). Fixed by binding the key to the token's algorithm family + fail-fast on key/alg mismatch; regression-tested. The full tenant-boundary security-reviewer pass (8 threat classes) is otherwise clean.
- **Open (deployment, → spec 11):** the JWT audience check is skipped when `PERSONA_API_JWT_AUDIENCE` is unset (the v0.1 default, since the auth provider is deferred). Set it in production once a provider is chosen.

## [0.7.0] — 2026-05-28

Spec 07 close-out. The production storage layer: a `PostgresBackend` transport (in `persona-core`, behind the `[postgres]` extra) plus the full SQL schema, Alembic migration, and row-level-security policies (in `persona-api`). The headline architectural decision is that there is **no** standalone `PostgresPGVectorStore` — the existing four typed stores compose the new transport unchanged, so policy/versioning/audit/decay are reused, not re-implemented. The `MemoryStore` protocol stays synchronous (D-07-1, psycopg3 sync); the spec's async §4 sketch was superseded.

### Added
- `persona.stores.backend.Backend` — a `@runtime_checkable` transport protocol (`upsert`/`query`/`get_all`/`delete_persona`/`delete_documents`) extracted from `ChromaBackend`'s real surface. `TypedStore` now composes any `Backend`, so Chroma and Postgres are Liskov-interchangeable (D-07-3). ([`backend.py`](packages/core/src/persona/stores/backend.py))
- `persona.stores.postgres.PostgresBackend` — the production transport (psycopg3 sync + SQLAlchemy Core + pgvector). Embeds at write via an injected `Embedder`, asserts the embedding dim is exactly 384 (fail-fast), round-trips `ChunkProvenance` through promoted columns, and populates `chunk.distance` from the cosine `<=>` operator. **No decay SQL** — `EpisodicStore`'s Python-side `exp(-elapsed/tau)` (D-01-4) re-rank is reused, giving automatic Chroma parity (the spec's §4.3 decay SQL is superseded). ([`postgres.py`](packages/core/src/persona/stores/postgres.py))
- `persona_api.db.models` — the canonical SQLAlchemy Core schema (11 tables). `memory_chunks` promotes the versioning/provenance fields (`logical_id`/`version`/`superseded_by` + `content_hash` + the `ChunkProvenance` fields) to indexed columns (D-07-4); user metadata lives in a `metadata` JSONB column; identity chunks store NULL provenance. Indexes: `(persona_id, kind)`, `(persona_id, kind, logical_id)`, a partial `WHERE superseded_by IS NULL` current-heads index, and an HNSW `vector_cosine_ops` index. Composite FK `(persona_id, owner_id) → personas` on `conversations`/`runs` (defence-in-depth, security finding 1). ([`models.py`](packages/api/src/persona_api/db/models.py))
- `persona_api.db.engine` — `create_db_engine`, `set_current_user`, and the `rls_connection` context manager. The RLS user id is set via `set_config('app.current_user_id', :uid, true)` (parameterised, transaction-local) — **not** `SET LOCAL ... = :uid`, which is a syntax error with a bound param (D-07-5, verified by spike). ([`engine.py`](packages/api/src/persona_api/db/engine.py))
- `persona_api.db.rls` — per-table RLS policy SQL with the correct FK-chain joins (personas/conversations/runs direct `owner_id`; messages/turn_logs → conversations; memory_chunks → personas; credits/credit_transactions → `user_id`). `ENABLE` + `FORCE ROW LEVEL SECURITY`; fail-closed `current_setting(...,true)`; `WITH CHECK` mirrors `USING`. ([`rls.py`](packages/api/src/persona_api/db/rls.py))
- `persona-api` Alembic env + `001_initial` migration — synchronous runner (`postgresql+psycopg://`), reads `DATABASE_URL`. `001_initial` creates the extension, all tables/indexes, and RLS policies in one atomic upgrade (RLS in `001`, no unsafe window). Real `downgrade`. ([`env.py`](packages/api/alembic/env.py), [`001_initial.py`](packages/api/alembic/versions/001_initial.py))

### Changed
- `persona.stores.base.TypedStore.__init__` — `backend` parameter widened from `ChromaBackend` to the `Backend` protocol (additive; the spec-01 store regression suite stays green). `delete()` calls the storage-neutral `delete_persona` (D-07-3). ([`base.py`](packages/core/src/persona/stores/base.py))
- `persona.stores.chroma.ChromaBackend` — `delete_collection` renamed to `delete_persona` (the Chroma `delete_collection` SDK call stays internal). ([`chroma.py`](packages/core/src/persona/stores/chroma.py))
- `packages/core/pyproject.toml` — the `[postgres]` extra swapped from `asyncpg`+`sqlalchemy[asyncio]` to `psycopg[binary]`+`sqlalchemy` (sync; D-07-1).
- Root `pyproject.toml` — depends on `persona-core[postgres]` so a plain `uv sync` installs the extra; added a mypy override ignoring `pgvector.*` missing stubs.
- `.env.example` / `alembic.ini` — `DATABASE_URL` dialect changed `postgresql+asyncpg://` → `postgresql+psycopg://`.
- `docker-compose.yml` — dropped the obsolete `version:` key.

### Security
- Row-level security on every tenant-scoped table; tenant isolation proven by adversarial integration tests (cross-tenant query returns zero rows; `WITH CHECK` blocks cross-tenant writes; fail-closed when the user GUC is unset). A `security-reviewer` pass surfaced a defence-in-depth gap (a tenant could attach a conversation/run to another tenant's persona via the single-column FK, even though RLS hid the row) — closed with the composite `(persona_id, owner_id)` FK and a regression test.

## [0.6.0] — 2026-05-28

Spec 06 close-out. The agentic loop (`persona_runtime.agentic`) — the plan-act-reflect execution engine for end-to-end tasks ("draft a complaint about my landlord refusing to fix mould"). Pure orchestration over specs 01–05; zero new dependencies. The simplest possible agent loop (architecture §5.2): one model decides at each step whether to call a tool, ask the user, or produce a final answer.

### Added
- `persona_runtime.agentic.errors` — `MaxStepsReachedError`, `RunCancelledError` (under `PersonaError`). Defined for spec-08's optional use; the loop itself returns a `Run` with a terminal `RunStatus` rather than raising (D-06-2). ([`errors.py`](packages/runtime/src/persona_runtime/agentic/errors.py))
- `persona_runtime.agentic.step` — `StepType` (StrEnum) + `Step` (frozen Pydantic). A step records its action, tool calls/results, question/answer, content, and per-step telemetry (`tier_used`/`tokens`/`latency_ms` — the v0.1 telemetry sink; no separate `StepLog` writer, D-06-3). ([`step.py`](packages/runtime/src/persona_runtime/agentic/step.py))
- `persona_runtime.agentic.run` — `RunStatus` (StrEnum) + `Run` (frozen Pydantic, UUID default id, tz-aware datetimes, JSON-serialisable per acceptance #10) + `CancelToken` (plain mutable control class — D-06-1). The loop holds mutable working state and emits the frozen `Run` at the end. ([`run.py`](packages/runtime/src/persona_runtime/agentic/run.py))
- `persona_runtime.agentic.events` — `RunEvent` (frozen Pydantic) + 12 typed classmethod constructors (`started`/`thinking`/`tool_calling`/`tool_result`/`asking_user`/`user_responded`/`reasoning`/`completed`/`cancelled`/`max_steps`/`error`/`finished`). The single place each event's `type`+`data` payload is defined; the API serialises these to SSE (§8). ([`events.py`](packages/runtime/src/persona_runtime/agentic/events.py))
- `persona_runtime.agentic.compactor.StepHistoryCompactor` — compacts step history at 80% of the tier budget (§6). Preserves the persona block + task (the floor, `context[0]`) and the recent tail verbatim (acceptance #8). The async-bridge is kept local (no shared `_bridge.py`): the loop pre-computes the small-tier summary and passes the compactor a resolved string (D-06-4). ([`compactor.py`](packages/runtime/src/persona_runtime/agentic/compactor.py))
- `persona_runtime.agentic.loop.AgenticLoop` — the keystone. `async run(task, on_event, user_respond, cancel_token) -> Run` runs the plan-act-reflect cycle: non-streaming `chat()` per step; classification via `[ASK_USER]`/`[FINAL]` markers + a question-mark heuristic fallback (no classifier); error recovery (a hallucinated/failed tool feeds back `ToolResult(is_error=True, ...)`, D-03-3; same bad name twice → a stronger instruction, §5.2); boundary-only cancellation (D-06-7); a best-effort frontier summary at `max_steps` (status `max_steps_reached`, never `completed` — D-06-2); the `use_skill` intercept (D-04-10); step-tier policy in the loop (`_tier_for_step` + a `force_frontier_tier` escape hatch, D-06-6); and an end-of-run episodic write tagging the chunk as a skill candidate for a future spec 13 (`source=agentic_run` + run/task/tools/steps/status metadata, D-06-8). `max_steps` default 20; no inner per-step tool-round cap (D-06-7). ([`loop.py`](packages/runtime/src/persona_runtime/agentic/loop.py))
- `persona_runtime.agentic.__init__` re-exports the public surface: `AgenticLoop`, `Run`, `RunStatus`, `Step`, `StepType`, `RunEvent`, `CancelToken`, `StepHistoryCompactor`, `MaxStepsReachedError`, `RunCancelledError`. ([`__init__.py`](packages/runtime/src/persona_runtime/agentic/__init__.py))

### Changed
- `packages/core/SPEC.md` — added an "Agentic loop (Spec 06)" subsection.
- `.env.example` — noted `max_steps` is a constructor default (20), no env knob.
- No new dependencies — spec 06 is orchestration over existing surfaces.

## [0.5.0] — 2026-05-28

Spec 05 close-out. `persona-runtime` — the conversation loop, prompt builder, router, tier registry, and per-turn logging. The first integration spec; composes specs 01–04 into a runnable turn loop. First code outside `persona-core`.

### Added
- `persona_runtime.errors.TierNotConfiguredError` — the one new runtime domain exception (D-05-2); everything else re-raises spec-01/02/03 domain exceptions unchanged (hexagonal). ([`errors.py`](packages/runtime/src/persona_runtime/errors.py))
- `persona_runtime.tier` — `TierConfig` (frozen dataclass) + `TierRegistry` (lazy-instantiate + cache via `load_backend`; `small→mid→frontier` fallback; single-backend fallback from `PERSONA_*`; `TierNotConfiguredError` if nothing resolves). `aclose()` duck-types backend cleanup (`getattr(backend, "aclose"/"disconnect")`) and is owned by the composition root, not the loop (D-05-3, D-05-4). `tier_registry_from_env()` presence-checks `<PREFIX>PROVIDER`. ([`tier.py`](packages/runtime/src/persona_runtime/tier.py))
- `persona_runtime.router.Router` — rule-based, no ML (architecture §5.3). Precedence: per-persona override → first-turn-frontier → boilerplate-small → persona-critical-frontier → mid default. `_is_persona_critical` derives keywords per-call from the persona's constraints + worldview (D-05-5); word-boundary regex. ([`router.py`](packages/runtime/src/persona_runtime/router.py))
- `persona_runtime.prompt` — `RetrievedContext` (frozen Pydantic bundle) + `PromptBuilder.build(...)`. System block in spec §5.1 order (identity → constraints → self-facts → worldview → episodic → skill index → active skill content → footer); worldview epistemic tags in parentheses. Receives already-budgeted `matched_skill_content: str` — no `SKILL_TOKEN_BUDGET` on the builder (the `SkillInjector` owns the 2000 budget; D-05-7). Context-window reduction drops episodic → worldview → self-facts; identity/constraints/skill-index are the never-truncated floor (spec §5.3). Token estimate via `persona.skills.count_tokens` (D-05-8). ([`prompt.py`](packages/runtime/src/persona_runtime/prompt.py))
- `persona_runtime.logging` — `TurnLog` (frozen Pydantic, not the spec's `@dataclass`; crosses the spec-08 Postgres boundary, D-05-9) + `TurnLogWriter` Protocol + `JSONLTurnLogWriter` (path mirrors D-01-6 audit convention; `PERSONA_TURNLOG_PATH` override) + `MemoryTurnLogWriter`. `_PRICE_TABLE` + `estimate_cost_cents` — hand-maintained estimate; unknown `(provider, model)` → `0.0` + warn-once (S05-3, D-05-10). ([`logging.py`](packages/runtime/src/persona_runtime/logging.py))
- `persona_runtime.loop.ConversationLoop` — the keystone. `async turn(conversation, user_message) -> AsyncIterator[StreamChunk]` runs the full spec §4.1 sequence. The sync/async summariser bridge (D-05-X): predicts compaction via `_will_compact` (replicating `manage()`'s boundary math, cross-checked by a lockstep test), pre-computes the small-tier summary, hands `manage()` a sync no-op assembler — never `asyncio.run()` in a sync callable. Unified `max_tool_rounds` counter for tool + use_skill re-prompts (one increment per round, D-05-11); use_skill intercept on `result.data["skill_name"]` with once-per-turn injection. Tool-call reconstruction from streamed `ToolCallDelta`s by `call_id` (D-05-13). Episodic write-back is the last step before the final chunk; a partially-consumed turn writes nothing (async-generator suspend, D-05-12). The loop receives the `Conversation`, never owns it (D-S05-4). ([`loop.py`](packages/runtime/src/persona_runtime/loop.py))
- `persona_runtime.__init__` re-exports the public surface: `ConversationLoop`, `PromptBuilder`, `RetrievedContext`, `Router`, `TierConfig`, `TierRegistry`, `tier_registry_from_env`, `TurnLog`, `TurnLogWriter`, `JSONLTurnLogWriter`, `MemoryTurnLogWriter`, `TierNotConfiguredError`. ([`__init__.py`](packages/runtime/src/persona_runtime/__init__.py))

### Changed
- `packages/runtime/pyproject.toml` — added `tiktoken>=0.7,<1` as a direct dependency (already transitive via `persona-core`; declared directly per engineering standards §5). No other new dependencies — spec 05 is pure orchestration.
- `packages/core/SPEC.md` — added a "Runtime (Spec 05)" subsection (the runtime is a separate consumer package; the dependency arrow points one way).
- `.env.example` — documented the tier-fallback semantics and added `PERSONA_TURNLOG_PATH`.

### Tests
- **96 new runtime tests** (7 errors + 12 tier + 25 router + 9 prompt + 14 logging + 19 loop + 5 integration + 5 end-to-end/context). Two are load-bearing: the boundary-prediction lockstep (loop `_will_compact` vs real `manage()` across K-1/K/K+1 × 3 configs) and the early-consumer-exit episodic-skip (acceptance #10).
- One `python-reviewer` pass on `loop.py`: one valid finding (round counter was incremented per-call, not per-round) fixed + regression-tested; the reviewer's other findings were verified non-bugs against the `manage()` source.
- Runtime test tree intentionally has **no `__init__.py`** (adding them collides `tests.conftest`/`tests.unit` with the core package); a `tests/conftest.py` puts the shared `_fakes` helper on `sys.path`.

### Documentation
- `docs/specs/spec_05/{spec_05_runtime.md, spec_05_kickoff.md, tasks.md, tasks.yaml, research.md, decisions.md, state.md, handover.md, README.md, closeout.md}` — full lifecycle of Spec 05 captured.
- D-05-1..D-05-13 + D-05-X + D-S05-4 added to root [`docs/DECISIONS.md`](docs/DECISIONS.md).

## [0.4.0] — 2026-05-27

Spec 04 close-out. Skills layer — scanner, injector, index renderer, `use_skill` synthetic activation tool, two built-in skill packs.

### Added
- `persona.skills/` package — skill scanner, injector, index renderer, `use_skill` synthetic tool. ([`packages/core/src/persona/skills/`](packages/core/src/persona/skills/))
- `persona.skills._tokens.count_tokens` — wraps `tiktoken cl100k_base` at module import; hard-imported (no `len // 4` fallback per D-04-2). Single module-level `_ENCODER` singleton; thread-safe. ([`skills/_tokens.py`](packages/core/src/persona/skills/_tokens.py))
- `persona.skills._frontmatter.parse_skill_markdown` — hand-rolled ~25-LOC YAML front-matter parser (D-04-3; declines `python-frontmatter` due to BOM silent-failure gap found in Phase 3 §2 research). Tolerates UTF-8 BOM and CRLF line endings; distinguishes all malformed cases with typed `SkillManifestError`. ([`skills/_frontmatter.py`](packages/core/src/persona/skills/_frontmatter.py))
- `persona.errors.SkillManifestError` — raised by the front-matter parser on malformed input. Includes `context["path"]` always and `context["reason"]` on YAML parse failures. ([`errors.py`](packages/core/src/persona/errors.py))
- `persona.skills.scanner.SkillScanner` — `scan(declared_skills, *, tool_allow_list)` with per-skill warn-and-skip envelope catching `SkillManifestError`, `ValidationError`, `KeyError` for missing required front-matter fields, and broad `Exception` (D-04-4); `BaseException` propagates. Silent skip on absent user `skills/` dir (D-04-5); same-name override of a built-in is WARNING-logged. Output preserves declared order. ([`skills/scanner.py`](packages/core/src/persona/skills/scanner.py))
- `persona.skills.index.render_skill_index(skills) -> str` — pure function producing the always-injected compact "available skills" Markdown block (D-04-6). Empty list returns empty string (no header). `when_to_use=None` skips the "Use when:" sub-line. ([`skills/index.py`](packages/core/src/persona/skills/index.py))
- `persona.skills.injector.SkillInjector` — `TOKEN_BUDGET = 2000` class constant (D-04-7, non-negotiable per architecture §5.1.2); `async inject(skill)` with verbatim pass-through / summariser-call / binary-search-truncation branches. Defensive: summariser returning over-budget output falls through to truncation. `MARKER = "\n\n[truncated]"`, ceil-bisection on character index (D-04-8); 16 tokeniser calls for 85 KB body. ([`skills/injector.py`](packages/core/src/persona/skills/injector.py))
- `persona.skills.use_skill_tool.make_use_skill_tool(skills)` — closure-based factory producing the synthetic `use_skill` `AsyncTool` via spec-03's `@tool` decorator unchanged (Pattern-1 activation; D-04-9). On valid skill name: returns `ToolResult(is_error=False, data={"skill_name": "X"})` for runtime interception. On unknown name: returns `ToolResult(is_error=True)` with sorted comma-joined available list. Exported from `persona.skills`, NOT auto-registered in `build_default_toolbox` (D-04-10; spec 05 composes). For non-native-tool backends, spec-02's prompt-shim JSON-block format `{"tool": "use_skill", "args": {...}}` (D-02-6) IS the activation channel — no new wire format. ([`skills/use_skill_tool.py`](packages/core/src/persona/skills/use_skill_tool.py))
- `persona.schema.skills.SkillSpec` extended additively (D-04-1) with `tools_required: list[str]`, `content: str`, `content_token_count: int` — all optional with defaults (`list()`, `""`, `0` respectively). Spec-01's four-field construction surface unchanged. ([`schema/skills.py`](packages/core/src/persona/schema/skills.py))
- Built-in skill packs `web_research` (2,804 tokens, exercises the over-budget injector path end-to-end) and `document_drafting` (1,151 tokens, exercises the verbatim pass-through path) under `persona/skills/builtin/`. Both regression-guarded by `tests/integration/test_builtin_skills.py::TestTokenCountRegressionGuards`. ([`skills/builtin/web_research/SKILL.md`](packages/core/src/persona/skills/builtin/web_research/SKILL.md), [`skills/builtin/document_drafting/SKILL.md`](packages/core/src/persona/skills/builtin/document_drafting/SKILL.md))
- `persona.skills.__init__` re-exports seven public names: `SkillSpec`, `SkillScanner`, `SkillInjector`, `render_skill_index`, `make_use_skill_tool`, `count_tokens`, `SkillManifestError`. ([`skills/__init__.py`](packages/core/src/persona/skills/__init__.py))

### Changed
- `tiktoken` dependency status changes from "parked" (D-01-11) to "live" — used by `persona.skills._tokens` for skill-content token counting. No version-pin change; `tiktoken>=0.7,<1` was already in core deps.
- `packages/core/SPEC.md` — added "Skills (Spec 04)" subsection summarising the package structure, public guarantees, and the seven D-04 decisions. `tiktoken` Dependencies-comment updated from "parked; spec 05 (prompt builder)" to "live in spec 04 (skill token-budget enforcement)".
- `.env.example` — added an informational comment block in the new "Skills (spec 04)" section noting that `TOKEN_BUDGET` is a module constant (no env knob in v0.1 per D-04-7) and that absent user `skills/` directories are silently skipped (D-04-5).

## [0.3.0] — 2026-05-27

Spec 03 close-out. Tools, MCP, and the Toolbox.

### Added
- `persona.tools.ToolDescriptor` Protocol (the metadata surface — `name`, `description`, `parameters_schema`) and `persona.tools.AsyncTool` Protocol (extends `ToolDescriptor` with `async execute(**kwargs) -> ToolResult`). Sibling to spec-01's sync `Tool` Protocol (D-03-2; spec-01's `Tool` is untouched). ([`tools/protocol.py`](packages/core/src/persona/tools/protocol.py))
- `@tool(name=..., description=...)` decorator wrapping an `async def` into an `AsyncTool`. JSON Schema synthesised via `pydantic.TypeAdapter`; argument model uses `ConfigDict(extra="forbid")` so typo'd kwargs from the model fail validation. Two catch sites — argument-validation errors AND body-raised `Exception` (not `BaseException`) — both produce `ToolResult(is_error=True, ...)`. `BaseException` propagates (D-03-5). ([`tools/protocol.py`](packages/core/src/persona/tools/protocol.py))
- `persona.tools.Toolbox` — registry + literal-only allow-list + async `dispatch`. `None` allow-list is permissive with a WARNING log (development convenience per D-03-7); production callers pass `persona.tools`. Duplicate tool names raise `ValueError`. `ToolNotAllowedError.context["allowed"]` carries a comma-joined string of available names per D-03-8. ([`tools/toolbox.py`](packages/core/src/persona/tools/toolbox.py))
- `format_tool_result(call, result, *, provider_name) -> ConversationMessage` — provider-aware formatter using a `match` statement on seven supported provider names. Anthropic (`tool_result` content block in user message), OpenAI / DeepSeek / Groq / Together (role=tool with `tool_call_id`), Ollama / local HF (shim plain-text). Unknown provider raises `ValueError` (D-03-6). ([`tools/formatting.py`](packages/core/src/persona/tools/formatting.py))
- Built-in tool `web_search` (D-03-9, D-03-10) — `make_web_search_tool(provider, api_key, http)` factory; `_SearchProvider` Protocol; `BraveSearchProvider` wired against `https://api.search.brave.com/res/v1/web/search` with `X-Subscription-Token` header; `TavilySearchProvider` and `SerpAPISearchProvider` raise `NotImplementedError` (caught by the `@tool` envelope → `ToolResult(is_error=True)`). Provider via `PERSONA_WEB_SEARCH_PROVIDER`; key via `PERSONA_WEB_SEARCH_API_KEY`. Structured results in `ToolResult.data["results"]`. ([`tools/builtin/web_search.py`](packages/core/src/persona/tools/builtin/web_search.py))
- Built-in tool `web_fetch` (D-03-11, D-03-12, D-03-24) — `httpx` + `trafilatura.extract(output_format="txt", favor_precision=True, include_comments=False, include_tables=False)`. Non-HTML content-type passes through via `Response.text`. Truncation past `max_chars` sets `truncated=True` + `data["original_length"]`. Scheme allow-list: `http`/`https` only; full SSRF guard deferred to spec 11. ([`tools/builtin/web_fetch.py`](packages/core/src/persona/tools/builtin/web_fetch.py))
- Sandbox path resolver `persona.tools._sandbox.resolve_sandbox_path(root, requested) -> Path` — pure function, no I/O. Rejects: NULL byte (D-03-15), >4096-char paths, mixed `\\` separator on POSIX, empty/whitespace, absolute paths (`PurePosixPath.is_absolute`), `.` / `./` root references, paths whose `.resolve(strict=False)` escapes `root.resolve()` (catches `..` traversal AND symlink escape). 55 adversarial tests written tests-first (Phase 1 refinement #8); two `security-reviewer` subagent passes (T09 + T10) with all findings addressed. `_preview()` strips control characters from user input before embedding in error context (security-review T09 Finding 1). ([`tools/_sandbox.py`](packages/core/src/persona/tools/_sandbox.py))
- Built-in tools `file_read` + `file_write` (D-03-16, D-03-17, D-03-18) — `make_file_read_tool(sandbox_root)` and `make_file_write_tool(sandbox_root, audit_logger, persona_id)` factories. `os.open(O_NOFOLLOW | ...)` closes the TOCTOU window between resolver and open. UTF-8 with `errors="replace"` for reads; 1 MB cap with `truncated=True` over. `file_write` mode `0o600`, emits one `ToolAuditEvent(action="write")` per successful write. Lone-surrogate `UnicodeEncodeError` and `os.write` `OSError` both caught and returned as clean `ToolResult(is_error=True, ...)` (security-review T10 Findings 5 + 10.2). ([`tools/builtin/file_read.py`](packages/core/src/persona/tools/builtin/file_read.py), [`tools/builtin/file_write.py`](packages/core/src/persona/tools/builtin/file_write.py))
- MCP client + adapter (D-03-19, D-03-20, D-03-21) — `mcp.client.streamable_http.streamablehttp_client` transport (NOT the deprecated `mcp.client.sse`). `MCPClient` uses `AsyncExitStack` for procedural-style lifecycle (`await client.connect()` / `disconnect()`). `MCPToolAdapter` wraps each discovered MCP tool as an `AsyncTool` named `mcp:<server>:<tool>` (literal allow-list per Phase 1 refinement #4). Graceful degradation `strict=False` for Toolbox auto-load. Audit events on connect / disconnect / server_unavailable; per-call dispatch audits skipped. Disconnection-like errors → `ToolResult(is_error=True, content="MCP server disconnected")`. `load_mcp_clients(servers, ...)` helper. ([`tools/mcp/client.py`](packages/core/src/persona/tools/mcp/client.py), [`tools/mcp/adapter.py`](packages/core/src/persona/tools/mcp/adapter.py))
- `persona.tools.audit` — dedicated tool-audit port (D-03-25, supersedes D-03-18's "reuse `AuditEvent`" recap). `ToolAuditEvent` Pydantic v2 model + `ToolAuditLogger(Protocol)` + `JSONLToolAuditLogger` / `MemoryToolAuditLogger` implementations. The JSONL logger documents single-process safety (security-review T10 Finding 7); hosted-service multi-process safety lands with the Postgres backend in spec 08. ([`tools/audit.py`](packages/core/src/persona/tools/audit.py))
- `build_default_toolbox(config, persona, *, tool_audit_logger) -> tuple[Toolbox, list[MCPClient]]` — composes the four built-in tools + connects MCP servers from `PersonaCoreConfig.mcp_servers_parsed`. Returns the toolbox and the MCP clients (so the caller can `await client.disconnect()` on shutdown). Graceful degradation per D-03-20. ([`tools/_factory.py`](packages/core/src/persona/tools/_factory.py))
- Two new domain exceptions: `MCPConnectionError`, `MCPServerUnavailableError` — flat under `PersonaError` per D-03-1. Re-exported from `persona.tools.errors`. ([`errors.py`](packages/core/src/persona/errors.py), [`tools/errors.py`](packages/core/src/persona/tools/errors.py))
- `persona.tools.__init__` re-exports 22 names — Protocols, `Toolbox`, `@tool`, formatter, the four built-in factories, MCP client + adapter, `build_default_toolbox`, audit Protocol + impls + event, and the five tool/MCP exceptions.

### Changed
- `persona.schema.tools.ToolResult` additively extended with `data: dict[str, Any] | None = None` and `truncated: bool = False` (D-03-3). `extra="forbid"` enforces that there is no separate `error` field — `is_error=True` + `content` is the single failure-truth.
- `persona.backends.types.tool_spec_from_tool()` parameter widened from `Tool` to `ToolDescriptor` — strictly additive (every `Tool` is a `ToolDescriptor`; every `AsyncTool` is too). No breaking change to spec-02's call sites.
- `PersonaCoreConfig` gained four spec-03 fields: `web_search_provider: Literal["brave", "tavily", "serpapi"]`, `web_search_api_key: SecretStr | None`, `tools_sandbox_root: Path` (default `./.persona_work` per D-03-23), `mcp_servers: str` (raw env value; the parsed dict is exposed via the `mcp_servers_parsed` property because Pydantic Settings JSON-pre-parses `dict[str, str]` env vars before validators run). ([`config.py`](packages/core/src/persona/config.py))
- `packages/core/pyproject.toml` — added `trafilatura>=2.0,<3` (web_fetch) and `mcp>=1.0,<2` (MCP client). Both core deps per D-03-12; transitive trees documented in [`docs/specs/spec_03/research.md`](docs/specs/spec_03/research.md) §2-3.
- `.env.example` — renamed `PERSONA_SEARCH_*` → `PERSONA_WEB_SEARCH_*` per Phase 1 refinement #7 (futureproofs against vector/code search later); added `PERSONA_TOOLS_SANDBOX_ROOT` and `PERSONA_MCP_SERVERS`.
- `packages/core/SPEC.md` — "Tools, MCP, and the Toolbox (Spec 03)" subsection added.

### Tests
- **214 new unit tests** across `tests/unit/tools/` (11 errors + 18 protocol + 20 decorator + 36 formatting + 20 toolbox + 17 web_search + 16 web_fetch + 55 sandbox + 30 file tools + 12 MCP adapter + 11 MCP client + 22 factory/config).
- Two `security-reviewer` subagent passes: T09 (sandbox resolver, 4 findings) + T10 (file tools, 10 findings — 1 HIGH, 2 MEDIUM, others LOW/accepted-risk). All actionable findings addressed in code; accepted-risk findings documented for spec 11.
- **682 unit + 28 integration + 26 contract = 736 total tests, all green.**
- All checks: `ruff check`, `ruff format --check`, `mypy --strict packages/core/src` clean (61 source files; was 47 after spec 02).

### Documentation
- `docs/specs/spec_03/{spec_03_tools.md, spec_03_kickoff.md, tasks.md, tasks.yaml, research.md, decisions.md, state.md, handover.md, README.md, closeout.md}` — full lifecycle of Spec 03 captured.
- D-03-1..D-03-25 added to root [`docs/DECISIONS.md`](docs/DECISIONS.md).

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
