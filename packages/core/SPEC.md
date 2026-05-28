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
- Conversation loop, router, agentic loop (spec 05/06).
- `PostgresPGVectorStore` (spec 07 — same protocol, different backend).
- HTTP API or web UI.

## Versioning

Spec 01 ships as `persona-core 0.1.0`. Subsequent specs that add to the public surface bump the minor version. Breaking changes bump the major.
