# Open Persona: Typed-memory AI persona platform with tier-routed model selection

Open Persona is a platform for building and running AI personas that maintain a
stable identity across long, multi-turn, tool-using conversations, including
real-time voice. The thing that makes it different from "ChatGPT with a system
prompt" is the **typed memory + tier-routed runtime**: a persona's identity,
self-facts, worldview, and episodic memory are structured, versioned, typed
stores (not free-text), and the runtime puts a right-sized model on each task.
Frontier models go where persona quality matters; smaller and cheaper models
go everywhere else. A voice trunk (LiveKit + streaming STT + streaming TTS)
layers sub-second-latency real-time conversation onto the same persona surface.

---

## Architecture

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │                         Web App (Next.js)                           │
   │    auth · persona authoring · chat UI · voice client (in dev)       │
   └──────────────────────────────┬──────────────────────────────────────┘
                                  │ HTTPS / SSE / OpenAPI
   ┌──────────────────────────────▼──────────────────────────────────────┐
   │                    Hosted API (FastAPI)                             │
   │  · users · personas · conversations · credits · audit log           │
   │  · /v1/personas/:id/chat   (SSE streaming)                          │       ┌──────────────────────────┐
   │  · /v1/personas/:id/run    (agentic task)                           │◀────▶ │   persona-voice trunk    │
   │  · /v1/personas/author     (LLM-assisted authoring)                 │       │  LiveKit substrate (V1)  │
   └──────────────────────────────┬──────────────────────────────────────┘       │  Streaming STT     (V2)  │
                                  │ in-process                                   │  Streaming TTS     (V3)  │
   ┌──────────────────────────────▼──────────────────────────────────────┐       │  Turn-taking       (V4)  │
   │              persona-runtime (Python)                               │       │  Reply producer    (V5)  │
   │  ┌────────────┐  ┌──────────┐  ┌─────────┐  ┌──────────────────┐    │       │  Frontend voice    (V6)  │
   │  │ Memory     │  │  Router  │  │ Toolbox │  │ History manager  │    │       └──────────────────────────┘
   │  │  identity  │  │ frontier │  │  web    │  │ summarise+compact│    │
   │  │  self      │  │ mid      │  │  fs     │  │ skill budgeter   │    │
   │  │  world     │  │ small    │  │  mcp    │  │                  │    │
   │  │  episodic  │  │          │  │  skills │  │                  │    │
   │  └────────────┘  └──────────┘  └─────────┘  └──────────────────┘    │
   │                AgenticLoop (plan → act → reflect)                   │
   └──────────────────────────────┬──────────────────────────────────────┘
                                  │
   ┌──────────────────────────────▼──────────────────────────────────────┐
   │           persona-core (Python library, source-available)           │
   │  · YAML schema · validation · registry                              │
   │  · four typed memory stores (Chroma + Postgres/pgvector)            │
   │  · model backend abstraction (frontier APIs + local HF + Ollama)    │
   │  · image-gen, sandbox, audit, logging, CLI                          │
   └─────────────────────────────────────────────────────────────────────┘
                │                                  │
                ▼                                  ▼
       ┌────────────────┐                ┌──────────────────────────────┐
       │  Postgres      │                │   Model providers            │
       │  + pgvector    │                │   Anthropic · OpenAI ·       │
       │  + object      │                │   DeepSeek · Groq · Together │
       │   storage      │                │   NVIDIA · OpenRouter ·      │
       │                │                │   Ollama · local HF          │
       └────────────────┘                └──────────────────────────────┘
```

Four layers, each only talking to the one below it. The voice trunk attaches
to the API layer and reuses the same persona / memory / runtime surface for
its reply producer. Voice is not a parallel stack; it's the same stack with
audio I/O wrapped around the turn loop.

---

## Status

### Shipped (v0.1, June 2026)

- `persona-core`: typed memory stores (identity / self_facts / worldview /
  episodic), versioned append-only updates with `history`/`rollback`, YAML
  schema + validator, eight model-provider backends, image generation,
  sandboxed code execution, CLI (`persona init / chat / run / serve`), audit
  log.
- `persona-runtime`: conversation loop, prompt builder with skill-token
  budgeting, summarise-and-compact history manager, tier router (frontier /
  mid / small) with multi-model-per-tier cross-provider fallback, agentic
  plan-act-reflect loop, tool dispatch.
- `persona-api`: FastAPI service with auth, persona CRUD, conversations,
  credits, audit log, SSE-streaming `/chat`, agentic `/run`, and LLM-assisted
  `/author`.
- `persona-web`: Next.js app with persona authoring, chat UI, and a billing
  dashboard stub.
- `persona-voice V1`: LiveKit substrate (rooms, agent worker, four-seam
  pipeline scaffold).
- `persona-voice V2`: Deepgram streaming STT.
- `persona-voice V3`: Cartesia Sonic streaming TTS plus per-persona voice as a
  first-class identity attribute.
- `persona-voice V4`: turn-taking + barge-in — the conversational state machine,
  automatic endpointing, fast-and-discriminating interruption, and full-loop
  latency ownership (the orchestration core; pure-Python on the V1/V2/V3 seams).

### In development

- `persona-voice V5`: model reply producer (wires the persona turn loop
  into the voice trunk).
- `persona-voice V6`: frontend voice client.

The four-layer text platform (`core` + `runtime` + `api` + `web`) is at v0.1
and usable end-to-end. The voice trunk is partially live; real-time
two-way conversation lands when V4-V6 complete.

---

## Quick start

You need Python 3.11+, [uv](https://docs.astral.sh/uv/), and Docker (for
Postgres + pgvector).

```bash
# 1. clone + install the workspace
git clone https://github.com/yasinhessnawi1/Open-Persona.git
cd open-persona
uv sync

# 2. spin up Postgres + pgvector for the hosted-path integration tests
docker compose up -d postgres

# 3. run the default test suite (unit + contract; integration + external skip)
uv run pytest

# 4. (optional) run the integration suite
uv run pytest -m integration

# 5. (optional) type-check + lint
uv run mypy packages/core/src --strict
uv run ruff check
```

Per-package quickstarts (install one package standalone, run the CLI, embed
the library in your own code) live in each package's own README. See the
table below.

For environment variables (provider keys, Postgres URLs, voice provider
credentials, feature toggles), copy `.env.example` to `.env` and fill in
what you need. Each section in `.env.example` is grouped by package and
documents the minimum set needed for that package to run.

---

## Packages

| Package | Description | License | Status |
| --- | --- | --- | --- |
| [`packages/core/`](packages/core/README.md) | Typed memory stores, persona schema, model backends, image-gen, sandbox, CLI. The library you `pip install persona-core` to get. | PolyForm-NC 1.0.0 (source-available, noncommercial) | Shipped (v0.1) |
| [`packages/runtime/`](packages/runtime/README.md) | Conversation loop, tier router, prompt builder, history manager, agentic loop, tool dispatch. | PolyForm-NC 1.0.0 (source-available, noncommercial) | Shipped (v0.1) |
| [`packages/voice/`](packages/voice/README.md) | LiveKit-based voice trunk: streaming STT, streaming TTS, turn-taking, real-time persona conversation. | PolyForm-NC 1.0.0 (source-available, noncommercial) | V1-V3 shipped, V4-V6 in development |
| [`packages/api/`](packages/api/) | Hosted FastAPI service: auth, persona CRUD, SSE-streaming chat, agentic run, LLM-assisted authoring. | Private | In development (license posture TBD) |
| [`packages/web/`](packages/web/README.md) | Next.js web app: persona authoring, chat UI, billing dashboard. | Private | In development |

Each package has its own `CHANGELOG.md`, `pyproject.toml`, and version line.
The workspace `pyproject.toml` at the repo root pins them together via uv
workspace.

---

## License

**Source-available subset (noncommercial use only):** `packages/core/`,
`packages/runtime/`, `packages/voice/` are licensed under
[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0).
Free for personal, research, evaluation, educational, and noncommercial
production use. Commercial use requires a separate license from the rights
holder.

**Private packages:** `packages/api/`, `packages/web/` are not currently
licensed for external use; their public license posture is to-be-determined
when the work matures.

---

## Contributing

Contributions are welcome on the three source-available packages (`core`,
`runtime`, `voice`) under the same PolyForm Noncommercial 1.0.0 license.
Please:

1. Open an issue first if the change is non-trivial. A quick design check
   saves both sides a round-trip.
2. Follow the existing engineering style: Python 3.11+, Pydantic v2 frozen
   models on every boundary, `mypy --strict` on `persona-core`, full
   docstrings on public APIs, `ruff check` + `ruff format` clean, tests
   required for new behaviour.
3. Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`,
   `chore:`), squash-merge to `main`.

See `pyproject.toml` for the canonical tooling configuration.

`persona-api` and `persona-web` are not accepting external contributions
yet. They're under active hardening and the surface is still moving.

---

## Roadmap

The shipped v0.1 is the floor, not the ceiling. The themes the next
releases work toward (in rough order):

- **Voice trunk completion**: turn-taking + barge-in, model reply
  producer, frontend voice client (V4-V6) so real-time two-way voice
  conversation is end-to-end usable.
- **Intelligent routing**: replace the static tier-config router with a
  signal-driven routing layer that scores each turn against model
  capabilities (cost, latency, reasoning depth, tool capability, vision,
  audio) and picks the right tier per turn.
- **Tools v2**: better tool authoring ergonomics, structured tool
  observations, tool-level budgeting and observability.
- **MCP v1**: first-class Model Context Protocol client so any MCP server
  in the wild becomes a persona-callable tool.
- **Rich output delivery**: inline rendering of images, files, diagrams,
  and other non-text artifacts in the chat surface.
- **Public license posture for `persona-api` + `persona-web`**, once the
  public-facing surfaces are stable.

Watch the [CHANGELOG](CHANGELOG.md) for what actually shipped, and the
per-package `CHANGELOG.md` files for per-surface detail.
