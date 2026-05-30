# Deploying `persona-api` to Fly.io

The Fly.io path — alternative to the Docker Compose VPS path
(`docker-compose.production.yml` + `bootstrap.sh` + `Caddyfile.example`). Fly's
edge terminates TLS automatically, so no Caddy is needed.

## What Fly buys you here

- **TLS for free** — `fly certs add api.openpersona.dev` and you're done.
- **Managed Postgres** — `fly pg create` + `fly pg attach` wires `DATABASE_URL`.
- **One-Machine deploy** — matches S08-4 / D-08-5 (in-process run bus, in-memory rate limiter).
- **Volume-backed audit logs** — survives Machine restarts.

## Prerequisites

- `flyctl` installed (`brew install flyctl`).
- `fly auth login` (you already have an account per the launch session).
- Repo cloned locally (Fly builds from your local context).

---

## 1. Create the app + Postgres + volume

```bash
# Pick your own app name (Fly requires globally-unique).
APP=open-persona-api
PG_APP=open-persona-db
REGION=fra            # Frankfurt — close to Vercel's fra1 (D-11-2)

# 1a. The API app.
flyctl apps create $APP --org personal

# 1b. The audit-log volume (small; the JSONL logs rotate).
flyctl volumes create persona_audit --app $APP --region $REGION --size 1 --yes

# 1c. Managed Postgres+pgvector cluster (single node — single-tenant v0.1).
flyctl pg create \
  --name $PG_APP \
  --region $REGION \
  --vm-size shared-cpu-1x \
  --initial-cluster-size 1 \
  --volume-size 3 \
  --image-ref flyio/postgres-flex:15

# 1d. Attach the cluster to the API app. This:
#     * creates a new DB user named after $APP
#     * grants it on a fresh DB named $APP
#     * sets DATABASE_URL on the API as a secret
flyctl pg attach $PG_APP --app $APP
```

The attached `DATABASE_URL` comes in `postgres://...` form; the API expects
the `postgresql+psycopg://` dialect. Patch it now:

```bash
# Read the existing DSN and rewrite the scheme.
ATTACHED=$(flyctl ssh console --app $APP -C 'printenv DATABASE_URL')
PATCHED=${ATTACHED/postgres:\/\//postgresql+psycopg://}
flyctl secrets set --app $APP DATABASE_URL="$PATCHED"
```

> **Aside on pgvector:** the `flyio/postgres-flex` image includes the
> `vector` extension binary. The Alembic migration runs
> `CREATE EXTENSION IF NOT EXISTS vector` for you (D-07-6).

---

## 2. Set the API secrets

Read each value from your `.env` (DeepSeek key, Anthropic key, etc.) and the
Clerk dashboard. Use `flyctl secrets set` (encrypted at rest, redacted in
logs).

```bash
flyctl secrets set --app $APP \
  PERSONA_PROVIDER=deepseek \
  PERSONA_MODEL=deepseek-chat \
  PERSONA_API_KEY=$DEEPSEEK_KEY \
  PERSONA_FRONTIER_PROVIDER=anthropic \
  PERSONA_FRONTIER_MODEL=claude-sonnet-4-6 \
  PERSONA_FRONTIER_API_KEY=$ANTHROPIC_KEY \
  PERSONA_MID_PROVIDER=deepseek \
  PERSONA_MID_MODEL=deepseek-chat \
  PERSONA_MID_API_KEY=$DEEPSEEK_KEY \
  PERSONA_SMALL_PROVIDER=deepseek \
  PERSONA_SMALL_MODEL=deepseek-chat \
  PERSONA_SMALL_API_KEY=$DEEPSEEK_KEY \
  PERSONA_WEB_SEARCH_API_KEY=$SEARCH_KEY \
  PERSONA_API_JWT_PUBLIC_KEY="$(cat packages/api/.secrets/clerk-jwt-public.pem)"
  # CORS — set this LATER (§5) once Vercel gives you the persona-web URL.
```

`PERSONA_API_JWT_AUDIENCE`, `PERSONA_API_JWT_ALGORITHMS`, `PERSONA_LOG_LEVEL`,
`PERSONA_AUDIT_ROOT`, `PERSONA_TURNLOG_PATH` are already set in `fly.toml`
under `[env]` — no action needed.

---

## 3. Deploy

```bash
# From the repo root (the Dockerfile's COPY paths assume that as context).
flyctl deploy -c deploy/fly.toml
```

The Machine will report unhealthy at first (no tables yet) — that's expected;
healthchecks turn green after step 4.

---

## 4. First-boot bootstrap — Postgres role + alembic + grants

Same three steps the local `bootstrap.sh` does, but via Fly tooling. **All
idempotent — safe to re-run.**

### 4a. Create the `persona_app` non-superuser role + grants

```bash
# Open psql against the Postgres cluster (NOT the API app).
flyctl pg connect --app $PG_APP -d $APP
```

At the prompt, paste:

```sql
-- Pick a strong password — you'll need it for APP_DATABASE_URL below.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persona_app') THEN
    CREATE ROLE persona_app LOGIN PASSWORD 'REPLACE_ME_PERSONA_APP_PW' NOSUPERUSER;
  END IF;
END$$;

GRANT USAGE ON SCHEMA public TO persona_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO persona_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO persona_app;
\q
```

### 4b. Run the migration (creates tables, RLS policies, indexes)

```bash
flyctl ssh console --app $APP \
  -C "sh -c 'cd /app/packages/api && uv run alembic -c alembic/alembic.ini upgrade head'"
```

### 4c. Grant on the now-existing tables

```bash
flyctl pg connect --app $PG_APP -d $APP
```

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO persona_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO persona_app;
\q
```

### 4d. Set `APP_DATABASE_URL` as a secret

Use the same hostname Fly composed for `DATABASE_URL`, swapping the user and
password:

```bash
# Read DATABASE_URL, extract host/port/db, splice persona_app credentials.
DSN=$(flyctl ssh console --app $APP -C 'printenv DATABASE_URL')
# DSN looks like: postgresql+psycopg://<app_user>:<app_pw>@<host>:5432/<db>
HOST_DB=$(echo "$DSN" | sed -E 's|.*@||')          # host:5432/db
flyctl secrets set --app $APP \
  APP_DATABASE_URL="postgresql+psycopg://persona_app:REPLACE_ME_PERSONA_APP_PW@${HOST_DB}"
```

The Machine restarts after `secrets set` — healthcheck should go green within
~30 s. Verify:

```bash
flyctl status --app $APP
curl -fsS https://$APP.fly.dev/healthz   # {"status":"ok","db":"connected"}
```

---

## 5. Custom domain + TLS (once DNS is in your control)

```bash
# A-record / AAAA-record / CNAME on api.openpersona.dev → $APP.fly.dev
flyctl certs add --app $APP api.openpersona.dev
flyctl certs check --app $APP api.openpersona.dev   # wait for "ready"
```

Then update CORS:

```bash
# Replace with the real Vercel URL from #11.
flyctl secrets set --app $APP \
  PERSONA_API_CORS_ORIGINS="https://persona.openpersona.dev,https://<your-vercel-app>.vercel.app"
```

---

## 6. Smoke test

```bash
# Unauth — 401 with structured body.
curl -fsS -i https://api.openpersona.dev/healthz
curl -i  https://api.openpersona.dev/v1/personas -H "Authorization: Bearer fake"
# expect: HTTP/2 401 + {"error":"authentication_error", ...}
```

If `/healthz` returns 200 and `/v1/personas` returns 401 with the structured
error envelope, the deploy is green. Move on to **#11 — Vercel** in
LAUNCH_CHECKLIST.md.

---

## Future migrations

After any `alembic` revision bump, repeat **4b** (and only 4b — the
`persona_app` role already exists, and the `ALTER DEFAULT PRIVILEGES` from 4a
makes new tables inherit grants automatically):

```bash
flyctl ssh console --app $APP \
  -C "sh -c 'cd /app/packages/api && uv run alembic -c alembic/alembic.ini upgrade head'"
```

## Troubleshooting

- **`relation "personas" does not exist`** — you forgot step 4b. Run it.
- **`permission denied for schema public`** — you forgot step 4a or 4c. Run both.
- **CORS preflight 403 from the web app** — `PERSONA_API_CORS_ORIGINS` isn't set, or the Vercel preview URL changed. Re-set the secret with the exact origin (no trailing slash).
- **First request after deploy stalls 10+ s** — bge-small loads weights on first encode (D-02-10 lazy pattern). Subsequent requests are fast. The healthcheck `grace_period = 30s` covers it.
- **OOM on soak / agentic runs** — bump `memory = "2048mb"` in `fly.toml` and redeploy.
