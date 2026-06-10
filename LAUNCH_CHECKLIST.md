# Launch Checklist

> Operator-facing launch checklist for Open Persona v0.1.
> Originally drafted at Spec 11 close-out per D-11-11 (agent/human line). This
> file lives at the repo root so deploy scripts + the CHANGELOG can reference
> it by a stable path. Created at Spec 19 Phase 5 land (2026-06-07) — Spec 11
> closeout authored the content prose but did not materialise the file on
> disk; Spec 19 corrects that omission while amending the hosting section for
> the V1 4-deployable shape (D-V1-X-hosting-cost-reckoning).

---

## Reference documents

- [Spec 11 close-out](docs/specs/phase1/spec_11/closeout.md) — agent/human line per item; the authoritative §10 walk.
- [Spec V1 close-out](docs/specs/phase2/spec_V1/closeout.md) — voice-layer 4-deployable amendment.
- [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md) — calendar-bound + event-driven commitments accumulated post-launch.
- [Spec 11 screencast shot-list](docs/specs/phase1/spec_11/screencast_shotlist.md) — §9 demo recording.

---

## Hosting topology (Spec 19 amendment 2026-06-07 — V1 4-deployable shape)

**Original Spec 11 D-11-1/2/3 shape (v0.1 pre-V1):** `persona-pg` + `persona-api`
deployed via `docker-compose.yml` on a single Hetzner VPS.

**Amended shape post-V1 (D-V1-X-hosting-cost-reckoning):** Spec V1 introduces
**2 additional deployables** — `persona-voice` agent worker + `livekit-server`
Go binary sidecar. The v0.1 production topology is now **4 deployables** in a
single `docker-compose.yml`:

1. **`persona-pg`** — Postgres 16 + pgvector; persistent volume.
2. **`persona-api`** — FastAPI (single uvicorn worker per S08-4); Spec 13 workspace volume mounted.
3. **`persona-voice`** — voice agent worker (4th uv workspace member shipped at V1).
4. **`livekit-server`** — `livekit/livekit-server:v1.12.0` Go binary sidecar (WebRTC transport).

**VPS sizing:** **Hetzner CPX31 (~$15/mo, 8 vCPU / 16 GB RAM)** likely
sufficient at v0.1 scale per LiveKit's documented 5–20 sessions/CPU-core
ceiling (40–160 sessions at 8 vCPU). Sized for the V1 voice load; persona-api
+ persona-pg shares the headroom comfortably.

**New operator commitments introduced by V1:**

- **TURN credentials rotation** — Cloudflare primary + Twilio fallback per D-V1-2.
- **LiveKit API key/secret rotation** — currently dev-mode `devkey/secret`;
  **MUST** be rotated before production exposure.
- **Voice-log JSONL rotation** — same operator pattern as turn_logs.
- **LiveKit Server long-run UDP-port state** — restart-cadence required for
  long-running dev-mode containers (see Spec V1 closeout "Known limitations").

Cross-reference: [Spec V1 closeout cross-spec ledger row "Spec 11 D-11-1/2/3 hosting topology amendment"](docs/specs/phase2/spec_V1/closeout.md).

---

## Launch checklist (§10 — Spec 11 reference)

Each row carries the agent/human line per D-11-11. **Authoritative walk lives
in [Spec 11 close-out §11 acceptance audit](docs/specs/phase1/spec_11/closeout.md).**
This file is the operator-side mirror.

| # | Item | Disposition | Notes |
|---|---|---|---|
| 1 | Example personas validate + run | agent-done | [`packages/core/examples/`](packages/core/examples/) + `test_examples_validate.py` |
| 2 | 100-turn soak passes | agent-done | `test_soak_conversation` SOAK_TURNS=100 — 19m26s |
| 3 | 15-step agentic soak | agent-done | `test_soak_agentic_run` |
| 4 | Credits zero-guard | agent-done | API: `credits_service.require_credits` pre-flight 402 |
| 5 | Deploy Grafana dashboards | prepared-human-executes | JSON shipped at `packages/api/dashboards/`; human imports |
| 6 | persona-core README under 200 lines | agent-done | 143 lines |
| 7 | Demo screencast shot-list | agent-done | [`screencast_shotlist.md`](docs/specs/phase1/spec_11/screencast_shotlist.md) |
| 8 | Record screencast | prepared-human-executes | Human records, edits, uploads, pastes URL into README |
| 9 | Flip persona-core repo public | prepared-human-executes | Irreversible; Apache 2.0 LICENSE + CI green |
| 10 | Deploy persona-api | prepared-human-executes | Dockerfile + Compose + env manifest shipped |
| 11 | Deploy persona-web | prepared-human-executes | `vercel.json` shipped; human connects Vercel |
| 12 | Tag `v0.1.0` | prepared-human-executes | After Playwright e2e + corpus eval + soak + Lighthouse |

---

## Pre-flight before tag (§9 — Spec 11 reference)

Four manual pre-tag items **NOT in CI** (documented honestly per D-11-11):

- Playwright e2e (needs live API + Clerk + DeepSeek + Docker)
- Authoring corpus eval (paid, manual)
- Soak suite (paid, manual)
- Lighthouse on `/chat` (manual)

CI matrix runs everything else (Python typecheck + lint + test + Web typecheck +
lint + build + Vitest).

---

*Last updated: 2026-06-07 — Spec 19 Phase 5 land. Original content authored at
Spec 11 close-out 2026-05-29; V1 hosting amendment added at Spec 19 close-out.*
