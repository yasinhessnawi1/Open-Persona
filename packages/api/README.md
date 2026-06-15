# persona-api

> Hosted FastAPI service for Open Persona.

**Status:** Private · In Development

## What it is

`persona-api` is the HTTP composition root that exposes `persona-core` +
`persona-runtime` over a REST + SSE surface. FastAPI on top of Postgres
(with `pgvector` extension) for persona storage, conversation history,
typed-memory chunks (the four-store typology + a `DocumentStore` sibling),
agentic-run state, turn logs, credits ledger, and rate-limit accounting.
Single uvicorn worker by design. The in-process agentic-run event bus and
the in-memory rate limiter assume one worker.

Routes (under `/v1`):

- `personas`: list / create / read / update / delete; YAML round-trip
- `conversations`: chat resource + SSE streaming + cascade delete
- `runs`: agentic-run create / SSE stream / cancel / ask-user reply
- `documents` + `uploads`: document ingestion (txt/md/code/csv/docx/xlsx/pdf)
  and image upload (vision)
- `imagegen`: image generation with pre-deduct credits + per-user
  advisory-lock cap. Persona create also auto-generates a demographic-safe
  avatar (free, fail-soft to null; bounded by
  `PERSONA_API_AVATAR_GEN_TIMEOUT_S`, default 25s) when none is supplied
- `artifacts`: chart + image serve
- `tools`: toolbox introspection
- `me`: credits balance + per-turn usage
- `health`: liveness + readiness

Auth via Clerk JWT (RS256, verified against the dashboard PEM via
`persona.auth.jwt_verifier`). Row-Level Security at the database layer
(`persona_app` non-superuser role; per-request user_id bound by RLS engine
context).

## Install

`persona-api` is **not yet open source** and **not** published to PyPI.
Development install from the workspace:

```bash
git clone <internal-repo-url>
cd open-persona
uv sync --all-packages
```

Service prerequisites:

- Postgres 16 with `pgvector` (use `docker compose up -d postgres` from the
  repo root)
- A Clerk dashboard JWT public key at
  `packages/api/.secrets/clerk-jwt-public.pem`
- A repo-root `.env` with `PERSONA_API_KEY` (or `PERSONA_{TIER}_API_KEY`
  triples) and any provider keys you intend to use

## Run

For local development against the `persona-pg` container and DeepSeek for
all tiers:

```bash
cd packages/api
bash run-local.sh       # uvicorn on :8000, single worker
```

Database migrations are explicit (never on container start):

```bash
uv run alembic -c packages/api/alembic.ini upgrade head
```

Production runs from the included `Dockerfile`:

```bash
docker build -t persona-api -f packages/api/Dockerfile .
docker run -p 8000:8000 --env-file .env persona-api
```

## Test

```bash
uv run pytest packages/api                          # unit (default)
uv run pytest packages/api -m integration           # needs Postgres
uv run pytest packages/api -m external              # needs live provider keys
uv run mypy packages/api/src
uv run ruff check packages/api
```

## Architecture role

`persona-api` is layer 2 of the Open Persona stack. It sits between
`persona-web` (the browser client) and `persona-runtime` + `persona-core`
(the orchestration + library). It owns: HTTP transport, JWT auth, RLS
context, Postgres persistence (Alembic migrations, RLS engine, transport
layer), the agentic-run event bus, the sandbox pool (E2B Code Interpreter
substrate; `e2b-code-interpreter[hosted]` extra), the credits service
(pre-deduct + refund), the rate limiter (in-memory by default; Postgres
store available), and produced-files persistence. The runtime is composed
inside `services/runtime_factory.py`; the API handles every concern the
runtime explicitly does not.

## Contribute

This package is currently private and **not** accepting external
contributions yet. The plan is to publish the API alongside the rest of
the stack once the v0.1 release-readiness arc closes; until then the source
is shared with collaborators only.
