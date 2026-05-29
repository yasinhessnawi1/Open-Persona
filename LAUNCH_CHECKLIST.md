# Launch Checklist — Open Persona v0.1.0

> The §10 acceptance items with each one's status — **agent-done**,
> **prepared-human-executes** (irreversible / credentialed), or
> **documented-limitation**. Per D-11-11 (the agent/human line) and the
> "honesty over polish" framing (§13), the close-out reports "N done, M
> prepared" — never a false "deployed."

**Date prepared:** 2026-05-29. **Project version (system tag):** `v0.1.0`.
**`persona-core` package version:** `1.0.0` (D-11-8 — first stable public Apache-2.0 release).

---

## Status legend

- **✅ DONE** — completed by the agent; no further human action.
- **🟦 PREPARED (human executes)** — agent has produced everything the human needs; the next step is a human action (credentials, billing, irreversible / public effect).
- **📄 DOCUMENTED (post-September)** — accepted limitation, written into the README's "Known Limitations" section so it isn't hidden.

---

## §10 items

### 1. ✅ All three example personas committed and tested

- [packages/core/examples/astrid_tenancy_law.yaml](packages/core/examples/astrid_tenancy_law.yaml)
- [packages/core/examples/kai_research.yaml](packages/core/examples/kai_research.yaml)
- [packages/core/examples/maren_writing_coach.yaml](packages/core/examples/maren_writing_coach.yaml)
- Loader/validate test: [test_examples_validate.py](packages/core/tests/unit/test_examples_validate.py) — 7/7 pass.

### 2. ✅ 100-turn soak test passed on each example persona — with measurement

- Harness: [packages/api/tests/soak/test_soak.py](packages/api/tests/soak/test_soak.py) (`@pytest.mark.external`).
- §4.1 assertions: zero 500s, identity at turn N, ≥8 compactions, early episodic retrievable, bounded prompt tokens — all met (Astrid, full-100 run measured during T03).
- Episodic-growth measurement recorded under T03 in [docs/specs/spec_11/state.md](docs/specs/spec_11/state.md).

### 3. ✅ 15-step agentic soak test passed (≥1 persona)

- Astrid 15-step `web_research + document_drafting` run completes cleanly (terminal `completed` or `max_steps_reached`).

### 4. ✅ Credits counter wired end-to-end (deduction + balance display + **zero-guard**)

- Pre-flight 402 on chat / runs / author / refine routes; post-success deduction unchanged (D-08-6); `low_balance` flag at <10k credits; web zero-state copy ("top-up coming soon, contact …").

### 5. 🟦 Three Grafana dashboards — JSON committed; production Grafana deploy is human

- §6.1 [01_per_persona_usage.json](packages/api/dashboards/01_per_persona_usage.json) ✅ rendered against soak data.
- §6.2 [02_routing_health.json](packages/api/dashboards/02_routing_health.json) ✅ rendered against soak data.
- §6.3 system-health 📄 documented post-September (no source telemetry today — D-11-5).
- **Human action:** stand up the production Grafana, create the `grafana_ro` `BYPASSRLS` role per [dashboards/README.md](packages/api/dashboards/README.md), import both JSON files, bind the `${DS_POSTGRES}` datasource.

### 6. ✅ Error-handling review complete — no stack traces in prod responses

- Structured error envelopes: `{"error": {"type", "message", "details"}}` everywhere (§7); FastAPI 422 keeps its native field-level `detail` (the spec-09 client already parses it). Verified in T06.

### 7. ✅ README committed; 🟦 awaiting human review/sign-off

- [packages/core/README.md](packages/core/README.md) — 143 lines (acceptance #6: ≤200), the 5 §8 questions answered + a Known Limitations section.

### 8. 🟦 Demo screencast — shot-list ready; **human records**

- [docs/specs/spec_11/screencast_shotlist.md](docs/specs/spec_11/screencast_shotlist.md) — the 8 steps with click paths and copy.
- **Human action:** rehearse the stack, record (OBS or QuickTime), edit to ≤4 min, upload, paste the URL into the `<screencast URL>` placeholder in `packages/core/README.md`.

### 9. 🟦 `persona-core` public, Apache 2.0, CI green

- Apache 2.0 [LICENSE](packages/core/LICENSE) present.
- **CI green covers** ([.github/workflows/ci.yml](.github/workflows/ci.yml)):
  - **Python:** `ruff check` + `ruff format --check` + `mypy` (core `--strict` + runtime `--strict` + api standard) + `pytest` (unit + contract — 1091 tests) + `pytest -m integration` against Docker Postgres + a provisioned `persona_app` non-superuser role (the RLS suite, 65 tests) on the **`postgresql+psycopg://`** dialect (D-07-1).
  - **Web:** `pnpm install --frozen-lockfile` + `pnpm typecheck` + `pnpm lint` (Biome) + `pnpm build` (Next 16 / Turbopack) + `pnpm test` (Vitest, 35 tests).
- **Manual pre-tag verifications NOT in CI** (same discipline as the agent/human line on deploy/tag/record):
  - **Playwright e2e** — needs the live API stack + a real Clerk instance + DeepSeek credits + Docker Postgres. Run from a developer machine: `cd packages/web && pnpm exec playwright test` after `bash packages/api/run-local.sh`.
  - **Authoring corpus eval** (spec 10) — paid, manual, per-model: `uv run pytest -m external packages/api/tests/integration/test_authoring_corpus_external.py`.
  - **Soak suite** (spec 11 §4.1) — paid, manual, ≥15 min per run: `SOAK_TURNS=100 uv run pytest -m external packages/api/tests/soak/`.
  - **Lighthouse on `/chat`** (spec 09 #10 follow-up) — manual: launch the stack, sign in to a stable demo account, run Lighthouse against the chat route.
- **Human action:** flip the GitHub repo from private to public (irreversible).

### 10. 🟦 `persona-api` running on a stable host

- Dockerfile: [packages/api/Dockerfile](packages/api/Dockerfile)
- Production Compose: [deploy/docker-compose.production.yml](deploy/docker-compose.production.yml)
- First-boot bootstrap: [deploy/bootstrap.sh](deploy/bootstrap.sh) — idempotent; provisions the `persona_app` non-superuser role, runs `alembic upgrade head`, grants schema/table/sequence privileges + sets default privileges. **Must run once after first `docker compose up -d`.** Without it the API 500s with `relation "personas" does not exist` (caught during Spec-11 pre-flight Gate 1).
- TLS / reverse proxy: [deploy/Caddyfile.example](deploy/Caddyfile.example) — Compose binds the API to `127.0.0.1:8000` (never directly exposed); Caddy terminates TLS via Let's Encrypt and proxies. SSE-safe (`flush_interval -1`).
- Env manifest: [deploy/.env.production.example](deploy/.env.production.example) — every variable the system reads, incl. **`PERSONA_API_JWT_AUDIENCE`** (T07 deploy-config fix for the open spec-08 MEDIUM).
- Recommendation: one small **cloud VPS** (Hetzner CX22 / DigitalOcean $6) running API + Postgres via Docker Compose, **single uvicorn worker** (S08-4). The API is CPU-bound; no GPU needed.
- **Human action** (the full sequence):
  ```bash
  # On the VPS (Ubuntu 24 LTS + Docker + Caddy installed):
  git clone <repo> open-persona && cd open-persona
  cp deploy/.env.production.example deploy/.env.production
  # …fill .env.production: POSTGRES_PASSWORD, PERSONA_APP_DB_PASSWORD, the Clerk PEM,
  # DeepSeek/Anthropic/search keys, PERSONA_API_JWT_AUDIENCE, PERSONA_API_CORS_ORIGINS.
  cd deploy
  docker compose -f docker-compose.production.yml up -d
  ./bootstrap.sh                                            # one-shot, idempotent
  # TLS: copy Caddyfile.example to /etc/caddy/Caddyfile, replace api.example.com,
  # set DNS A-record `api.openpersona.dev → <VPS IP>`, then:
  sudo systemctl reload caddy
  # Verify:
  curl -fsS https://api.openpersona.dev/healthz             # {"status":"ok","db":"connected"}
  ```

### 11. 🟦 `persona-web` deployed (Vercel)

- Vercel config: [packages/web/vercel.json](packages/web/vercel.json) — framework, build, env-var notes.
- **Human action:** connect the GitHub repo to Vercel, set the Clerk `NEXT_PUBLIC_*` keys + `NEXT_PUBLIC_PERSONA_API_BASE_URL` to the VPS API URL, set the API's `PERSONA_API_CORS_ORIGINS` to the Vercel origin, deploy.

### 12. 🟦 Git tags `v0.1.0` on all three repos

- Three repos in v0.1: `persona-core` (public), `persona-api` (private/public), `persona-web` (private).
- **Human action:** after #9–11 are live and the screencast is uploaded, push `v0.1.0` tags. **Irreversible — review before push:**

```bash
git tag -s v0.1.0 -m "Open Persona v0.1.0"
git push origin v0.1.0
```

---

## Documented limitations (carried into the public README)

- **Episodic eviction** post-September; age-based key planned (importance unbacked).
- **§6.3 system-health dashboard** post-September (telemetry-middleware-dependent).
- **File-tool intermediate-path TOCTOU** accepted for single-tenant.
- **Clerk JWT static-PEM** — JWKS-by-`kid` rotation post-September.
- **Single API worker** required by the in-process agentic event bus.
- **Anthropic native-tool-result path** not soak-verified; prefer DeepSeek for tool-heavy demos.

---

## Pre-flight (run by the human before #10–12)

CI now covers the green-light bar end-to-end (see #9). Re-run it locally as a
fresh sanity-check, then run the four MANUAL items CI deliberately doesn't:

```bash
# === Mirrors CI (must match the live CI run) ===
uv run ruff check && uv run ruff format --check
uv run mypy packages/core/src packages/runtime/src --strict
uv run mypy packages/api/src
uv run pytest                                                      # 1091 unit+contract
uv run pytest -m integration                                       # 65 against Docker Postgres (needs persona_app role)
cd packages/web && pnpm install --frozen-lockfile
pnpm typecheck && pnpm lint && pnpm build && pnpm test             # Next 16 build + Vitest

# === MANUAL (NOT in CI — needs live stack / paid / human-in-the-loop) ===
# 1. Playwright e2e — needs live API + Clerk + DeepSeek + Docker.
docker start persona-pg && bash packages/api/run-local.sh &
cd packages/web && pnpm dev &
pnpm exec playwright test                                          # spec-09 e2e suite

# 2. Authoring corpus eval (spec 10, paid, per-model).
uv run pytest -m external packages/api/tests/integration/test_authoring_corpus_external.py

# 3. Soak suite (spec 11 §4.1; paid, ~20 min for the full 100-turn).
SOAK_TURNS=100 uv run pytest -m external packages/api/tests/soak/

# 4. Lighthouse on /chat (spec 09 #10 follow-up — sign in to a stable demo account first).
# Manual: run Lighthouse against http://localhost:3000/chat/<an existing conversation id>.
```

If any of the above fail, **stop**, fix, and re-run before tagging.

---

## How the close-out should read

The audit (§11) is reported as the final state of this checklist. The
expected headline: **6 agent-completed (#1, #2, #3, #4, #6, #7-write); 6
prepared-and-handed-off (#5-deploy, #8 record, #9 public, #10 deploy, #11
deploy, #12 tag).** Honest, not "12/12 deployed."
