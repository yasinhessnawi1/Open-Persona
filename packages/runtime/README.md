# persona-runtime

> Conversation loop, prompt builder, router, and agentic engine for Persona.
> Source-available; noncommercial use only.

**Status:** PolyForm Noncommercial 1.0.0 · Source Available (Noncommercial Use Only)

## What it is

`persona-runtime` is the orchestration layer that turns a `persona-core`
persona into a running conversational agent. It owns six things and nothing
else:

- **`ConversationLoop`**: the one-turn keystone. Retrieve typed-memory
  context, manage history (summarise-and-compact at K=10, keep last 5
  verbatim), build the prompt, route, stream-generate with a tool-call
  sub-loop, write the turn back to the episodic store.
- **`PromptBuilder`** + `RetrievedContext`: assembles the system prompt
  from identity + constraints + retrieved chunks + skill index, with a
  context-window budget reducer.
- **`retrieve_context`** (`persona_runtime.retrieval`): the per-turn
  conditioning retrieval (identity via `get_all`, the rest via `query`),
  extracted from the conversation loop so the voice trunk (`persona-voice` V5)
  shares the *same* conditioning — never reimplemented. The loop delegates to
  it; an optional `identity=` hook lets a long-lived session cache the
  constant identity read.
- **Routing**: `Router` (`@runtime_checkable` Protocol) with two concrete
  implementations. `HeuristicRouter` (rule-based, per-turn,
  per-persona-overridable) and `UnifiedRouter` (two-layer: hard-filter via
  `apply_constraint_filter` then sweet-spot scoring, with bounded fallback
  and per-tier metadata).
- **`IntelligentRouter`** (opt-in, Spec 23): after the rule-based router picks
  the tier, scores the candidate models in that tier's MODELS list on
  cost / quality / latency (+ a hard capability gate) using published metadata
  and picks the best — deterministic, no ML. Off by default
  (`routing.intelligent.enabled` in the persona YAML); degrades to the
  rule-based slot-0 model on a metadata miss.
- **`TierRegistry`**: lazy-cached backend registry per tier
  (`frontier` / `mid` / `small`); configured via `PERSONA_{TIER}_*`
  env triples; small→mid→frontier fallback; cross-provider multi-model
  per tier (Spec 20).
- **`AgenticLoop`**: the plan-act-reflect cycle in `persona_runtime.agentic`.
  One model decides at each step whether to call a tool, ask the user, or
  produce a final answer; `[ASK_USER]` / `[FINAL]` markers as the primary
  classification signal; step-history compaction at the tier budget;
  cancel-token boundary; terminal status (`completed` /
  `max_steps_reached` / `cancelled` / `error`) authoritative.
- **`TurnLog`** + `JSONLTurnLogWriter` / `MemoryTurnLogWriter`: per-turn
  telemetry record (model, tokens, cost, routing decision, latency,
  fallback) durable to JSONL or held in memory for tests.

The runtime depends only on `persona-core`; it does not depend on the API
or web app. The composition root (the API in production, the CLI for
local use, the tests in CI) owns the `Conversation` object and the
`TierRegistry` lifecycle. The loop itself is stateless per request.

## Install

From PyPI (planned):

```bash
pip install persona-runtime
```

Workspace development:

```bash
git clone https://github.com/yasinhessnawi1/Open-Persona.git
cd open-persona
uv sync --all-packages
```

## Run

`persona-runtime` is a library with no CLI of its own. Compose it on top of
`persona-core`:

```python
import asyncio
from pathlib import Path

from persona.schema.persona import Persona
from persona.schema.conversation import Conversation, ConversationMessage
from persona.registry import PersonaRegistry
from persona.stores.chroma import ChromaMemoryStore
from persona.tools.toolbox import Toolbox
from persona_runtime import (
    ConversationLoop, PromptBuilder, Router, TurnLog, tier_registry_from_env,
)


async def main() -> None:
    persona = Persona.from_yaml(Path("examples/astrid_tenancy_law.yaml"))
    registry = PersonaRegistry(store=ChromaMemoryStore.local("./.persona-data"))
    registry.load(persona)
    tiers = tier_registry_from_env()

    loop = ConversationLoop(
        registry=registry,
        tiers=tiers,
        router=Router(),
        prompt_builder=PromptBuilder(),
        toolbox=Toolbox.empty(),
    )

    conversation = Conversation.new(persona_id=persona.id)
    user = ConversationMessage(role="user", content="Hva sier husleieloven om mugg?", created_at=None)
    async for chunk in loop.turn(conversation, user):
        print(chunk.delta, end="", flush=True)
    await tiers.aclose()


asyncio.run(main())
```

Env vars (per tier; see `.env.example` at the repo root):

```
PERSONA_FRONTIER_PROVIDER=anthropic   PERSONA_FRONTIER_MODEL=claude-opus-...
PERSONA_MID_PROVIDER=deepseek         PERSONA_MID_MODEL=deepseek-chat
PERSONA_SMALL_PROVIDER=groq           PERSONA_SMALL_MODEL=llama-...
```

A single `PERSONA_PROVIDER` + `PERSONA_MODEL` + `PERSONA_API_KEY` pair is
the fallback when no per-tier vars are set.

## Test

```bash
uv run pytest packages/runtime                      # unit (default)
uv run pytest packages/runtime -m integration       # integration
uv run mypy packages/runtime/src
uv run ruff check packages/runtime
```

## Architecture role

`persona-runtime` is layer 3 of the Open Persona stack. It sits directly
above `persona-core` and below `persona-api`; the API composes the
runtime, attaches it to HTTP routes, and persists the per-request state
(conversation, run, turn-log, event bus). The runtime contains zero HTTP,
zero database client, zero secrets. Every collaborator is injected by the
composition root.

## Contribute

Contributions welcome under the same PolyForm Noncommercial 1.0.0 license.
The package is source-available for noncommercial use; commercial use
requires a separate license (contact the rights holder). Issues and pull
requests welcome at
[github.com/yasinhessnawi1/Open-Persona](https://github.com/yasinhessnawi1/Open-Persona).
See [CHANGELOG.md](CHANGELOG.md) for the spec-by-spec history.
