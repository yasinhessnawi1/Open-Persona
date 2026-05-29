# persona-core

> Open-source Python library for building AI personas with typed memory and
> tier-routed model selection. Apache 2.0.

`persona-core` lets you author a persona as a single YAML document (identity,
constraints, self-facts, worldview claims with epistemic tags, allowed tools
and skills) and run it against any of seven model providers — Anthropic,
OpenAI, DeepSeek, Groq, Together, local Ollama, or local HF — with four
typed memory stores (identity, self-facts, worldview, episodic) backed by
Chroma or Postgres + pgvector. The full system architecture, including the
hosted API and web app that compose this library, lives at
[ARCHITECTURE.md](https://github.com/yasinhessnawi1/open-persona/blob/main/docs/ARCHITECTURE.md).

## Install

```bash
pip install persona-core                 # core + Chroma + a frontier SDK
pip install persona-core[local]          # adds torch / transformers for HF local inference
pip install persona-core[postgres]       # adds psycopg + pgvector for the Postgres backend
```

Python ≥3.11.

## Use

### 1. Create a persona

```bash
persona init        # interactive prompts → astrid.yaml
```

Or copy one of the [committed examples](examples/) (the three personas the
launch screencast demonstrates):

- [`examples/astrid_tenancy_law.yaml`](examples/astrid_tenancy_law.yaml) — Norwegian tenancy-law assistant
- [`examples/kai_research.yaml`](examples/kai_research.yaml) — domain-agnostic research assistant
- [`examples/maren_writing_coach.yaml`](examples/maren_writing_coach.yaml) — writing coach (tool-free)

### 2. Chat with it from the terminal

```bash
export PERSONA_API_KEY=<your-deepseek-or-other-key>
export PERSONA_PROVIDER=deepseek         # or anthropic / openai / groq / together / ollama / local
export PERSONA_MODEL=deepseek-chat
persona chat examples/astrid_tenancy_law.yaml
```

Multi-turn conversations stay coherent — history is summarised at K=10 with
the last five turns kept verbatim; the persona's identity block, constraints,
and relevant retrieved facts/worldview/episodic chunks are re-injected every
turn. Full transcripts always go to the episodic store.

### 3. Use it programmatically

```python
import asyncio
from pathlib import Path

from persona.schema.persona import Persona
from persona.backends import OpenAICompatibleBackend, BackendConfig
from persona.schema.conversation import ConversationMessage

async def main() -> None:
    persona = Persona.from_yaml(Path("examples/astrid_tenancy_law.yaml"))
    backend = OpenAICompatibleBackend(BackendConfig(provider="deepseek", model="deepseek-chat"))
    system_prompt = f"You are {persona.identity.name}, {persona.identity.role}.\n{persona.identity.background}"
    reply = await backend.chat([
        ConversationMessage(role="system", content=system_prompt, created_at=None),
        ConversationMessage(role="user", content="Hva sier husleieloven om mugg?", created_at=None),
    ])
    print(reply.content)

asyncio.run(main())
```

For the full runtime loop with router, tool dispatch, episodic write-back and
turn logging, see the `persona-runtime` package and the hosted-API service.

## Hosted version

The hosted web app composes `persona-core` with `persona-runtime` and
`persona-api`: sign up, describe a persona in one sentence, watch the
authoring flow produce a YAML, edit in the structured form, chat with
streaming SSE + tool-call cards, and launch multi-step agentic runs. See the
[demo screencast](<screencast URL>) and the architecture document for the
eight-step flow.

## Architecture in one paragraph

A persona is a YAML document. The runtime loads it, builds a system prompt
from the identity + constraints + retrieved typed-memory chunks, hands a tier-
appropriate request to the configured model backend, dispatches any tool
calls, and writes the resulting turn to the episodic store. Tier routing
(small / mid / frontier) is rule-based, per-turn, and overridable per
persona. Memory stores are deterministic, append-only with versioning, and
audited. Skills are bundled by name and injected token-budget-aware (2k cap).
See [ARCHITECTURE.md](https://github.com/yasinhessnawi1/open-persona/blob/main/docs/ARCHITECTURE.md) for
the full picture.

## Contribute

Issues and PRs welcome — start with [CONTRIBUTING.md](https://github.com/yasinhessnawi1/open-persona/blob/main/CONTRIBUTING.md).

Dev setup:

```bash
git clone https://github.com/yasinhessnawi1/open-persona.git
cd open-persona
uv sync --all-packages
uv run pytest                              # unit + contract (skips integration/external)
uv run ruff check && uv run mypy packages/core/src --strict
```

## Known Limitations (v0.1.0, September 2026)

Honesty over polish — what isn't fixed in v0.1 is documented here, not hidden.

- **Episodic eviction is post-September.** At v0.1 demo scale (~100
  chunks/conversation) growth is bounded; a soak run measured it. An
  age-based `evict()` is the planned v1.1 addition; the spec's
  importance-based key was unbacked (neither write path stores `importance`).
- **System-health (§6.3) dashboard is post-September.** §6.1 (per-persona
  usage) and §6.2 (routing health) ship as committed Grafana JSON. §6.3
  (per-endpoint p99, error rate, provider availability) needs request-
  telemetry middleware no table currently captures.
- **File-tool intermediate-path TOCTOU** is accepted for single-tenant CLI.
  Multi-tenant hosting needs `openat2(RESOLVE_NO_SYMLINKS)` (Linux) — post-September.
- **JWT key rotation is manual.** Production verifies against a static Clerk
  PEM. JWKS-by-`kid` rotation is the post-September hardening; rotate the
  PEM manually for now.
- **Single API worker.** The in-process agentic-run event bus and the
  in-memory rate limiter require one uvicorn worker. A Redis-backed
  multi-worker deploy is post-September.
- **Anthropic native tool-result path is not soak-verified.** DeepSeek is the
  demo-primary model; Anthropic is the outage backup. The Anthropic assistant
  tool_use block is emitted correctly, but the tool-result block uses a
  passthrough user-message encoding rather than a structured top-level block.
  Prefer DeepSeek for tool-heavy demos.

## License

Apache 2.0. See [LICENSE](LICENSE).
