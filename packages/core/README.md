# persona-core

> Source-available Python library for building AI personas with typed memory
> and tier-routed model selection. Noncommercial use only.

**Status:** PolyForm Noncommercial 1.0.0 · Source Available (Noncommercial Use Only)

## What it is

`persona-core` lets you author a persona as a single YAML document (identity,
constraints, self-facts, worldview claims with epistemic tags, tools, skills)
and run it against any of seven model providers (Anthropic, OpenAI, DeepSeek,
Groq, Together, NVIDIA, OpenRouter, local Ollama, or local HF), with four
typed memory stores (identity, self-facts, worldview, episodic) backed by
ChromaDB or Postgres + pgvector. Ships the schema, the four memory stores
behind a `MemoryStore` protocol, the backend layer behind a `ChatBackend`
protocol, a sandboxed tool layer (`Toolbox`, MCP client, a known-tool
catalog, and built-in tools — `web_search` / `web_fetch` / `file_read` /
`file_write` / `calculator` / `datetime` / `currency_convert` / `regex_match` /
`json_query` / `text_diff` / `text_summarize` / `render_diagram`), a
`WorkspacePersister` protocol that lets byte-producing tools surface persisted
artifacts (`PersistedArtifact` on `ToolResult.artifacts`), a skills layer
(`SkillScanner` + `SkillInjector` + skill composition + a `skills.toml`
catalog + four built-in skill packs), a vision
layer (`ImageContent` + `ImageBackend`), document ingestion + generation,
a code-execution sandbox protocol, an `AuditLogger` protocol with a JSONL
default, per-component loguru logging, and a `persona` CLI. Every other
Open Persona package depends on this one; this one depends on nothing inside
Open Persona.

## Install

```bash
pip install persona-core                 # core + Chroma + frontier SDKs
pip install persona-core[local]          # adds torch / transformers for local HF inference
pip install persona-core[postgres]       # adds psycopg + pgvector for the Postgres backend
pip install persona-core[sandbox]        # adds docker SDK for LocalDockerSandbox
```

Python ≥ 3.11.

For workspace development from the monorepo:

```bash
git clone https://github.com/yasinhessnawi1/Open-Persona.git
cd open-persona
uv sync --all-packages
```

## Run

Author and chat with a persona from the terminal:

```bash
persona init                                      # interactive → astrid.yaml
persona validate examples/astrid_tenancy_law.yaml
export PERSONA_PROVIDER=deepseek
export PERSONA_MODEL=deepseek-chat
export PERSONA_API_KEY=<your-key>
persona chat examples/astrid_tenancy_law.yaml
persona audit examples/astrid_tenancy_law.yaml    # tail the JSONL audit log
```

Three example personas ship in [`examples/`](examples/):
`astrid_tenancy_law.yaml` (Norwegian tenancy-law assistant),
`kai_research.yaml` (research assistant), `maren_writing_coach.yaml`
(tool-free writing coach).

Programmatic use:

```python
import asyncio
from pathlib import Path

from persona.schema.persona import Persona
from persona.backends import OpenAICompatibleBackend, BackendConfig
from persona.schema.conversation import ConversationMessage

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

For the full conversation loop with router, tool dispatch, episodic
write-back and per-turn logging, compose with `persona-runtime`.

## Test

```bash
uv run pytest packages/core                          # unit + contract (default)
uv run pytest packages/core -m integration           # needs Postgres in Docker
uv run mypy packages/core/src --strict
uv run ruff check packages/core
```

## Highlights

- Frozen Pydantic v2 boundary models, `extra="forbid"` everywhere
- Deterministic chunk IDs: `{persona_id}::{store_kind}::{index:04d}`
- Versioned append-only stores; identity is immutable at runtime
- SHA-256 `content_hash` on every chunk; one `AuditEvent` per mutation
- Three-source write policy: `system` / `user` / `persona_self`
- 2k-token-budgeted skill injection (`SkillInjector.TOKEN_BUDGET`); depth-3
  skill composition (cycle detection + shared budget); `skills.toml` catalog
  with `collection:` refs
- Native tool calls (Anthropic / OpenAI / DeepSeek / Groq / Together / NVIDIA /
  OpenRouter) + prompt-shim fallback (Ollama / HF local)
- MCP Streamable HTTP client + adapter, plus **built-in MCP servers**
  (`time` / `calculator` / `filesystem` / `weather`) as thin FastMCP
  Streamable-HTTP subprocesses, indexed by a declarative `mcp_catalog.toml`
- Sandboxed file tools (path resolver rejects `..`, abs paths, symlink
  escape, NUL bytes, mixed separators)
- General-utility built-in tools: `calculator` (safe AST eval), `datetime`,
  `currency_convert`, `regex_match` (RE2, ReDoS-immune), `json_query`
  (JMESPath), `text_diff`, `text_summarize`; a `TOOL_CATALOG` enumerates the
  full set for persona-driven tool selection
- Four built-in skill packs: `web_research`, `data_analysis`,
  `document_generation` (one parameterized skill spanning docx/pdf/pptx/xlsx/
  md/txt), `code_review` (deprecated `*_generation` / `document_drafting`
  names still resolve via the alias shim)
- Image generation backends (OpenAI gpt-image-1, fal.ai Flux 1.1 [pro])
  with three-layer safety + categorical hard-line filter; plus
  `craft_avatar_prompt` — a deterministic, demographic-safe avatar-prompt
  crafter (role-anchored; appearance only when declared in `visual_style`)
- Vision input (`ImageContent`) + document ingestion + a `CodeSandbox`
  protocol with `LocalDockerSandbox` reference implementation

## Architecture role

`persona-core` is layer 4 of the Open Persona stack: the source-available
foundation. A persona is a YAML document; the schema, the typed memory
stores, the model-provider adapters, and the tool/skill machinery all live
here. `persona-runtime` composes the orchestration loop on top of this
library; `persona-api` exposes it over HTTP; `persona-web` is the browser
front-end. The dependency arrow points one way: `persona-core` imports
nothing from the upper layers.

## Contribute

Contributions welcome under the same PolyForm Noncommercial 1.0.0 license.
The package is source-available for noncommercial use; commercial use
requires a separate license (contact the rights holder). Issues and pull
requests welcome at
[github.com/yasinhessnawi1/Open-Persona](https://github.com/yasinhessnawi1/Open-Persona).
See [LICENSE](LICENSE) and the package
[`SPEC.md`](SPEC.md) for the public surface.
