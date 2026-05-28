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

## Model backends (Spec 02)

Spec 02 adds `persona.backends/` — a single async `ChatBackend` Protocol with three concrete implementations behind a `load_backend(config)` factory:

```
persona.backends
├── protocol.py    ChatBackend (Protocol; async chat + chat_stream)
├── config.py      BackendConfig (Pydantic Settings, PERSONA_* env)
├── types.py       ChatResponse, StreamChunk, TokenUsage, ToolSpec, ToolCallDelta
├── errors.py      ProviderError, AuthenticationError, RateLimitError,
│                  ModelNotFoundError, BackendTimeoutError
├── openai_compat.py  OpenAICompatibleBackend (anthropic SDK + openai SDK)
├── ollama.py      OllamaBackend (raw httpx; shim tool-calling by default)
└── hf_local.py    HFLocalBackend (lazy weight load; [local] extras only)
```

Public guarantees:
- One Protocol; every backend implements `chat()` and `chat_stream()`.
- Tool calls native where the provider supports them (Anthropic, OpenAI, allow-listed DeepSeek/Groq/Together models); prompt-based JSON-block shim everywhere else.
- Credentials env-only (`PERSONA_API_KEY`, optional `PERSONA_BASE_URL`); never logged.
- Construction-time `AuthenticationError` for missing keys (fail fast). `HFLocalBackend` lazy-loads weights on first call.

See [`docs/specs/spec_02/spec_02_backends.md`](../../docs/specs/spec_02/spec_02_backends.md) and [`docs/specs/spec_02/decisions.md`](../../docs/specs/spec_02/decisions.md) for the full surface.

## Tools, MCP, and the Toolbox (Spec 03)

Spec 03 adds `persona.tools/` — the layer that lets a persona act on the world. Built around two Protocols and a single registry:

```
persona.tools
├── protocol.py        ToolDescriptor (metadata), AsyncTool (async execute), @tool decorator
├── formatting.py      format_tool_result(call, result, *, provider_name) -> ConversationMessage
├── toolbox.py         Toolbox (registry + literal-only allow-list + dispatch)
├── errors.py          re-exports ToolNotAllowedError, ToolExecutionError, SandboxViolationError,
│                      MCPConnectionError, MCPServerUnavailableError
├── _sandbox.py        path resolver; pure function; tests-first, security-reviewed
├── _factory.py        build_default_toolbox(config, persona, *, audit_logger)
├── builtin/
│   ├── web_search.py        Brave default; _SearchProvider Protocol; Tavily/SerpAPI stubs
│   ├── _search_providers.py internal provider clients
│   ├── web_fetch.py         httpx + trafilatura; truncation via ToolResult.truncated
│   ├── file_read.py         sandboxed; UTF-8 with errors=replace; 1 MB cap
│   └── file_write.py        sandboxed; emits AuditEvent on every write
└── mcp/
    ├── client.py            MCPClient (Streamable HTTP via mcp.client.streamable_http)
    └── adapter.py           MCPToolAdapter (wraps server tools as AsyncTool)
```

`persona.schema.tools.ToolResult` extends additively with `data: dict[str, Any] | None` and `truncated: bool` (D-03-3). `is_error=True` + `content=<message>` remains the single failure-truth — no separate `error` field.

Public guarantees:
- One Protocol pair: `ToolDescriptor` (metadata) + `AsyncTool` (async `execute`). Spec-01's `Tool` Protocol kept as a sibling (D-03-2).
- `@tool` decorator catches argument-validation errors AND function-body exceptions; both return `ToolResult(is_error=True, ...)` (D-03-5).
- Literal-only allow-list (no wildcards). `None` is permissive with a WARNING log — development convenience only (D-03-7).
- File tools sandboxed via a path resolver that rejects `..`, absolute paths, NULL bytes, mixed `\\` separators on POSIX, paths >4096 chars, and symlinks escaping the sandbox root (D-03-13..D-03-15).
- MCP uses Streamable HTTP only in v0.1 (D-03-19); legacy SSE deprecated upstream; stdio deferred. Graceful-degradation `strict=False` for Toolbox auto-load (D-03-20).
- Audit events on `file_write` and MCP `connect`/`disconnect`/`server_unavailable` only — per-call dispatch audits skipped (D-03-21).

See [`docs/specs/spec_03/spec_03_tools.md`](../../docs/specs/spec_03/spec_03_tools.md) and [`docs/specs/spec_03/decisions.md`](../../docs/specs/spec_03/decisions.md) for the full surface.

## Skills (Spec 04)

Spec 04 adds `persona.skills/` — the layer that turns a chatbot into an agent that can complete a task A-to-Z. A skill is a directory containing a `SKILL.md` (YAML front matter + Markdown body); the persona's `skills: [...]` list declares which to load.

```
persona.skills
├── _tokens.py            count_tokens() — wraps tiktoken cl100k_base
├── _frontmatter.py       parse_skill_markdown(path) -> (dict, body)
├── scanner.py            SkillScanner.scan(declared_skills, *, tool_allow_list)
├── index.py              render_skill_index(skills: list[SkillSpec]) -> str  (pure)
├── injector.py           SkillInjector.TOKEN_BUDGET=2000; async inject(skill)
├── use_skill_tool.py     make_use_skill_tool(skills) -> AsyncTool factory
└── builtin/
    ├── web_research/SKILL.md       (over-budget stub; exercises summariser/truncator)
    └── document_drafting/SKILL.md  (under-budget stub; exercises verbatim pass-through)
```

`persona.schema.skills.SkillSpec` extends additively with `tools_required: list[str]`, `content: str`, `content_token_count: int` (D-04-1). Existing spec-01 fields (`name`, `description`, `path`, `when_to_use`) unchanged.

Public guarantees:
- `SkillScanner` reads declared skills, parses YAML front matter (hand-rolled ~25-LOC parser per D-04-3), validates `tools_required` against the persona's tool allow-list (WARN if missing), and emits one `SkillSpec` per discovered skill. Per-skill `Exception` envelope — warn-and-skip on missing-on-disk OR malformed YAML OR `SkillSpec` validation error (D-04-4). Absent user `skills/` dir is silently skipped; same-name override of a built-in is WARNING-logged (D-04-5).
- `render_skill_index(skills)` produces the always-injected compact "available skills" Markdown block. Pure function — no I/O, no clock, no state (D-04-6). Empty list returns empty string (no header).
- `SkillInjector.TOKEN_BUDGET = 2000` — class constant, non-negotiable in v0.1 per architecture §5.1.2 (D-04-7). `async inject(skill)`: verbatim pass-through under budget; summariser call if over and a summariser was injected; binary-search truncation on character index with marker `"\n\n[truncated]"` otherwise (D-04-8).
- `make_use_skill_tool(skills)` produces a synthetic `use_skill` `AsyncTool` (Pattern-1 activation; Pattern 2 string-matching deferred entirely per D-04-9). The tool returns `ToolResult(data={"skill_name": ...})` on valid skill activation for the runtime to intercept. **Exported from `persona.skills`**, NOT auto-registered in `build_default_toolbox` — spec 05's runtime composes when `persona.skills` is non-empty (D-04-10; mirrors D-03-2 sibling pattern).
- For non-native-tool backends (Ollama default + HF local), the spec-02 prompt-shim JSON-block wire format `{"tool": "use_skill", "args": {...}}` (D-02-6) IS the activation channel. No new wire format introduced.
- Two built-in skill packs ship: `web_research` (>2000 tokens, exercises over-budget injector path end-to-end) and `document_drafting` (<2000 tokens, exercises verbatim pass-through). Real polish content lands in week 14.

See [`docs/specs/spec_04/spec_04_skills.md`](../../docs/specs/spec_04/spec_04_skills.md) and [`docs/specs/spec_04/decisions.md`](../../docs/specs/spec_04/decisions.md) for the full surface.

## Runtime (Spec 05) — a separate package

Spec 05 introduces **`persona-runtime`** (`packages/runtime/`), the first code outside `persona-core`. It is a **consumer** of this package — it imports the stores, backends, toolbox, skills, and history manager and defines only orchestration. `persona-core` does not depend on `persona-runtime`; the dependency arrow points one way (architecture §3, hexagonal layering). This subsection is recorded here because `SPEC.md` is the project's package spec; the runtime has no separate `SPEC.md` in v0.1.

The runtime ships four orchestration types:

```
persona_runtime
├── errors.py    TierNotConfiguredError (the only new runtime exception; D-05-2)
├── tier.py      TierConfig + TierRegistry (lazy-cache, small→mid→frontier fallback, aclose())
├── router.py    Router.choose(persona, message, conversation) -> "frontier"|"mid"|"small"
├── prompt.py    PromptBuilder.build(...) + RetrievedContext; context-window budget reduction
├── logging.py   TurnLog (Pydantic) + TurnLogWriter Protocol + JSONL writer + cost table
└── loop.py      ConversationLoop.turn(conversation, user_message) -> AsyncIterator[StreamChunk]
```

Key contracts:
- **The turn sequence** (architecture §5.1, spec §4.1): retrieve context (identity `get_all` + self_facts/worldview/episodic `query(persona_id, msg, top_k=3)`) → manage history → build prompt → route → stream-generate with a tool-call sub-loop → episodic write-back → final chunk. The loop **receives** the `Conversation`, never owns it (D-S05-4) — stateless per request; the composition root (API / CLI / tests) loads and persists it.
- **The sync/async summariser bridge** (D-05-X): the history manager stays sync + pure; the loop pre-computes the small-tier summary (`await`) on boundary-crossing turns and hands `manage()` a sync no-op assembler. Never `asyncio.run()` inside the sync callable.
- **Routing** (architecture §5.3): rule-based, no ML. Per-persona override → first-turn-frontier → boilerplate-small → persona-critical-frontier → mid default.
- **Tier config** via `PERSONA_{FRONTIER,MID,SMALL}_{PROVIDER,MODEL,API_KEY}` env triples; fallback small→mid→frontier; single-backend fallback from `PERSONA_PROVIDER` when no tiers set (D-05-3). `TierRegistry.aclose()` is owned by the composition root, not the loop (D-05-4).
- **Skill budget** lives in ONE place — `SkillInjector.TOKEN_BUDGET` (spec 04). The `PromptBuilder` receives already-budgeted content; it does not re-enforce (D-05-7).
- The CLI is **not** rewired to the full loop in spec 05 — it stays on its spec-02 direct path; the API (spec 08) is the first real composition root.

See [`docs/specs/spec_05/spec_05_runtime.md`](../../docs/specs/spec_05/spec_05_runtime.md) and [`docs/specs/spec_05/decisions.md`](../../docs/specs/spec_05/decisions.md) for the full surface.

## Agentic loop (Spec 06) — `persona_runtime.agentic`

Spec 06 adds the **plan-act-reflect execution loop** for end-to-end tasks, in the `persona_runtime.agentic` subpackage. It is the *simplest possible* agent loop (architecture §5.2): one model decides at each step whether to call a tool, ask the user, or produce a final answer — no multi-agent orchestration, no graph-of-thought. The value is in the error-handling and budget management around the loop, not the loop body (~150 lines).

```
persona_runtime.agentic
├── errors.py     MaxStepsReachedError, RunCancelledError (defined; the loop returns a Run, doesn't raise — D-06-2)
├── step.py       StepType (StrEnum) + Step (frozen Pydantic; carries per-step telemetry — D-06-3)
├── run.py        RunStatus (StrEnum) + Run (frozen Pydantic) + CancelToken (plain mutable class — D-06-1)
├── events.py     RunEvent (frozen Pydantic) + typed constructors, serialised to SSE by the API (§8)
├── compactor.py  StepHistoryCompactor — compacts step history at the tier budget (§6)
└── loop.py       AgenticLoop.run(task, on_event, user_respond, cancel_token) -> Run
```

Key contracts:
- **The cycle** (architecture §5.2, spec §4.1): `for step in range(max_steps)` — check cancellation at the step boundary (never mid-step; D-06-7 keeps a step = one `chat()` call), choose a tier (`_tier_for_step`, in the loop — not the chat Router; D-06-6), generate via **non-streaming `chat()`**, classify the action (`tool_call` / `ask_user` / `final` / `reasoning`), dispatch tools and feed results back, compact if over budget, repeat. `max_steps`=20 is the only budget (no inner tool-round cap; D-06-7).
- **Action classification** (spec §4.2): `[ASK_USER]` / `[FINAL]` markers are the primary signal (injected by the loop, D-06-5); a simple question-mark heuristic is the fallback. **No classifier** (architecture §5.3). A marker-ignoring mid-tier model is mitigated by forcing frontier (D-06-6), not a smarter detector.
- **Error recovery** (spec §5): a failed/hallucinated tool feeds back a `ToolResult(is_error=True, content=...)` (`tool_name` required — D-03-3); the *model* decides how to recover. Same hallucinated name twice → a stronger instruction.
- **Step-history compaction** (spec §6): reuses the D-05-X async-bridge *idiom* locally (the loop pre-computes the small-tier summary; the compactor takes a resolved string — D-06-4). The persona block and task description are never touched (the run's invariant floor).
- **Terminal status is authoritative:** `RunStatus` distinguishes `completed` / `cancelled` / `max_steps_reached` / `error`; a best-effort max-steps summary never reads as `completed` (D-06-2).
- **End-of-run episodic write-back** tags the chunk as a skill candidate for a future spec 13 (`source="agentic_run"`, `run_id`, `task`, `tools_used`, `steps`, `status`; D-06-8), one combined chunk, `source=SYSTEM`, `written_by="agentic.run"`.
- **Spec 08 owns** what the loop does not: per-step `Run` persistence, the `user_respond` blocking wiring (the loop just `await`s the injected callback; D-06-10), `RunEvent`→SSE serialisation, and the `TierRegistry` lifecycle.

See [`docs/specs/spec_06/spec_06_agentic.md`](../../docs/specs/spec_06/spec_06_agentic.md) and [`docs/specs/spec_06/decisions.md`](../../docs/specs/spec_06/decisions.md) for the full surface.

## Dependencies

```
pydantic>=2.7,<3
pydantic-settings>=2.3,<3
chromadb>=1.0,<2
sentence-transformers>=3.0,<4
typer>=0.12,<1
pyyaml>=6.0,<7
loguru>=0.7,<1
httpx>=0.27,<1       # live in spec 02 (OllamaBackend)
tiktoken>=0.7,<1     # live in spec 04 (skill token-budget enforcement)
anthropic>=0.30,<1   # spec 02 (Anthropic SDK)
openai>=1.30,<2      # spec 02 (OpenAI/DeepSeek/Groq/Together)
trafilatura>=2.0,<3  # spec 03 (web_fetch HTML extraction)
mcp>=1.0,<2          # spec 03 (MCP client; Streamable HTTP transport)
```

Optional extras:
- `[local]` — torch, transformers, bitsandbytes, accelerate (for `HFLocalBackend` in spec 02).
- `[postgres]` — asyncpg, sqlalchemy[asyncio], pgvector (for spec 07's PostgresPGVectorStore).

Dependency rationale lives in [`docs/specs/spec_01/research.md`](../../docs/specs/spec_01/research.md) §1.

## Test strategy

- **`tests/unit/`** — pure unit tests, all external deps mocked. Run on every push, under 60 seconds.
- **`tests/integration/`** — real ChromaDB persistence round-trips. Marked `@pytest.mark.integration`. Skipped by default.
- **`tests/contract/`** — verifies every concrete `MemoryStore` honours the protocol contract.

Coverage target: every public class and function has at least one test.

## What this package does *not* contain

- Tool implementations (spec 03).
- Skill implementations (spec 04).
- Conversation loop, router, agentic loop (spec 05/06 — these live in `persona-runtime`, not `persona-core`).
- `PostgresPGVectorStore` (spec 07 — same protocol, different backend).
- HTTP API or web UI.

## Versioning

Spec 01 ships as `persona-core 0.1.0`. Subsequent specs that add to the public surface bump the minor version. Breaking changes bump the major.
