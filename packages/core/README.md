# persona-core

> The MIT-licensed Python library for building AI personas with typed memory and
> tier-routed model selection.

**License:** [MIT](LICENSE) — free for any use, including commercial.

`persona-core` is the foundation of [Open Persona](../../README.md): the
source-available, OSI-licensed engine that every other package builds on, and
that depends on nothing else in the project.

## What it is

A persona is a single typed YAML document — identity, constraints, self-facts,
worldview claims (with epistemic tags), tools, skills, and routing preferences.
`persona-core` turns that document into a running, memory-having, tool-using agent
you can drive from Python or the terminal. It ships:

- the **persona schema** + validator + registry (frozen Pydantic v2 boundary
  models, `extra="forbid"`, deterministic chunk IDs);
- four **typed memory stores** (identity / self_facts / worldview / episodic)
  behind a `MemoryStore` protocol, with a file-based **Chroma** backend (default,
  zero-infra) and a **Postgres + pgvector** backend (hosted);
- a **model backend** layer behind a `ChatBackend` protocol — Anthropic, OpenAI,
  DeepSeek, Groq, Together, NVIDIA, OpenRouter (native tool calls), plus local
  **Ollama** and local **Hugging Face** (prompt-shim fallback);
- a sandboxed **tool** layer (`Toolbox`, an MCP client, a known-tool catalog, and
  built-in tools) and a **skills** layer (`SkillScanner` + `SkillInjector` +
  composition + a `skills.toml` catalog + built-in skill packs);
- an **image-generation** layer, **vision** input, document ingestion +
  generation, a **code-execution sandbox** protocol, an `AuditLogger` protocol
  with a JSONL default, per-component loguru logging, and the **`persona` CLI**;
- a **durable-job contract** (`persona.jobs`) — the job model + state machine,
  frozen payloads, lease/retry policies, and a typed handler registry that the
  hosted worker composes (the queue + worker live in `persona-api`).

## Install

```bash
pip install persona-core                 # core + Chroma + frontier provider SDKs
pip install persona-core[local]          # + torch / transformers for local HF inference
pip install persona-core[postgres]       # + psycopg + pgvector for the Postgres backend
pip install persona-core[sandbox]        # + docker SDK for the LocalDockerSandbox
pip install persona-core[turbovec]       # + turbovec for the optional quantized graph index
```

Python ≥ 3.11. For workspace development from the monorepo:

```bash
git clone https://github.com/yasinhessnawi1/Open-Persona.git
cd Open-Persona
uv sync --all-packages
```

## Quickstart

Author and chat with a persona from the terminal — no API or web app required:

```bash
persona init                                      # interactive → a persona.yaml
persona validate examples/astrid_tenancy_law.yaml
export PERSONA_PROVIDER=deepseek
export PERSONA_MODEL=deepseek-chat
export PERSONA_API_KEY=<your-key>
persona chat examples/astrid_tenancy_law.yaml     # local REPL chat
persona run examples/astrid_tenancy_law.yaml "Draft a complaint about my landlord"
persona audit examples/astrid_tenancy_law.yaml    # tail the JSONL audit log
```

Three example personas ship in [`examples/`](examples/):
`astrid_tenancy_law.yaml` (Norwegian tenancy-law assistant), `kai_research.yaml`
(research assistant), and `maren_writing_coach.yaml` (tool-free writing coach).

## Usage

```python
import asyncio
from pathlib import Path

from persona.schema.persona import Persona
from persona.schema.conversation import ConversationMessage
from persona.backends import OpenAICompatibleBackend, BackendConfig


async def main() -> None:
    persona = Persona.from_yaml(Path("examples/astrid_tenancy_law.yaml"))
    backend = OpenAICompatibleBackend(
        BackendConfig(provider="deepseek", model="deepseek-chat")
    )
    system = f"You are {persona.identity.name}, {persona.identity.role}."
    reply = await backend.chat([
        ConversationMessage(role="system", content=system, created_at=None),
        ConversationMessage(role="user", content="Hva sier husleieloven om mugg?", created_at=None),
    ])
    print(reply.content)


asyncio.run(main())
```

For the full conversation loop — router, tool dispatch, episodic write-back,
per-turn logging — compose `persona-core` with
[`persona-runtime`](../runtime/README.md).

## Capabilities

- **Typed memory, versioned.** Identity is immutable at runtime; self_facts,
  worldview, and episodic are append-only with `history()` and `rollback()`. Every
  write is tagged with its source — `system` / `user` / `persona_self` — under a
  per-store update policy, with SHA-256 `content_hash` and exactly one `AuditEvent`
  per mutation.
- **Eight+ model providers** behind one protocol — native tool calls for Anthropic
  / OpenAI / DeepSeek / Groq / Together / NVIDIA / OpenRouter, plus a prompt-shim
  fallback for local Ollama / HF. Embeddings via `bge-small-en-v1.5` (384-dim),
  recorded in the schema for re-index safety.
- **Tools.** Built-ins include `web_search`, `web_fetch`, sandboxed `file_read` /
  `file_write` (the path resolver rejects `..`, absolute paths, symlink escape, NUL
  bytes, mixed separators), `calculator` (safe AST eval), `datetime`,
  `currency_convert`, `regex_match` (RE2, ReDoS-immune), `json_query` (JMESPath),
  `text_diff`, `text_summarize`, and `render_diagram`. A `TOOL_CATALOG` enumerates
  the full set for persona-driven tool selection.
- **MCP.** A Streamable-HTTP MCP client + adapter, plus built-in MCP servers
  (`time` / `calculator` / `filesystem` / `weather`) as thin FastMCP subprocesses,
  indexed by a declarative `mcp_catalog.toml`.
- **Skills.** Four built-in packs — `web_research`, `data_analysis`,
  `document_generation` (one parameterized skill spanning docx / pdf / pptx / xlsx
  / md / txt), and `code_review`. 2k-token-budgeted injection
  (`SkillInjector.TOKEN_BUDGET`), depth-3 composition (cycle detection + shared
  budget), `collection:` refs, and an alias shim so deprecated skill names still
  resolve.
- **Image generation** (OpenAI gpt-image-1, fal.ai Flux 1.1 [pro]) with a
  three-layer safety + categorical hard-line filter, plus `craft_avatar_prompt` —
  a deterministic, demographic-safe avatar-prompt crafter.
- **Vision + documents + sandbox.** `ImageContent` vision input, document ingestion
  and generation, and a `CodeSandbox` protocol with a `LocalDockerSandbox`
  reference implementation.
- **Knowledge graph** (`persona.graph`). A user-scoped "bigger brain" all of a
  user's personas read from and write to: concept-nodes connected by typed links
  (semantic / entity / temporal / causal), kept coherent by canonical-entity
  resolution (deterministic, LLM-free) + accumulate-via-merge, with Postgres as the
  source of truth and an optional turbovec quantized in-RAM dense index (pgvector is
  the default; 4-bit + mandatory exact-rerank). RLS-isolated per user; configured
  via `PERSONA_GRAPH_*` env vars. The foundation of the K-track (hybrid retrieval,
  write paths, graph-aware prompts, wellbeing, graph UI).

## Architecture role

`persona-core` is the bottom layer of the Open Persona stack — the
source-available foundation. A persona is a YAML document; the schema, the typed
memory stores, the model-provider adapters, and the tool/skill machinery all live
here. [`persona-runtime`](../runtime/README.md) composes the orchestration loop on
top; `persona-api` exposes it over HTTP; `persona-web` is the browser front-end.
The dependency arrow points one way — this library imports nothing from the upper
layers.

It is also where the **community edition** does its persistence: the file-based
Chroma backend holds typed memory locally with zero infrastructure. The
Postgres + pgvector backend is the same `MemoryStore` interface, swapped in for the
cloud edition.

## Test

```bash
uv run pytest packages/core                 # unit + contract (default)
uv run pytest packages/core -m integration  # needs Postgres in Docker
uv run mypy packages/core/src --strict
uv run ruff check packages/core
```

## License

`persona-core` is licensed under the **MIT License** — free for any use, including
commercial. See [LICENSE](LICENSE). The application layer of Open Persona
(`persona-api`, `persona-web`) is separately licensed PolyForm Noncommercial
1.0.0; see the [root README](../../README.md) for the full per-package table.

## Links

- [Open Persona — root README](../../README.md)
- [`persona-runtime`](../runtime/README.md) — the conversation / agentic engine
- [`persona-voice`](../voice/README.md) — the real-time voice trunk
- [CHANGELOG](CHANGELOG.md)
