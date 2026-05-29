# Open Persona — Observability Dashboards (Spec 11 §6)

Two committed Grafana dashboards for the v0.1 launch, reading from the live
`persona-api` Postgres. The third dashboard from the spec (§6.3, system health)
is **documented as post-September** below — it needs request-telemetry the
current schema doesn't capture (D-11-5).

| File | Spec | Data source |
|---|---|---|
| [`01_per_persona_usage.json`](01_per_persona_usage.json) | §6.1 — per-persona usage | `personas`, `conversations`, `turn_logs`, `memory_chunks` |
| [`02_routing_health.json`](02_routing_health.json) | §6.2 — routing health | `turn_logs` |

## Setup — the read-only role (D-11-5)

A plain `SELECT`-only role is **not enough**: the tenant tables `FORCE ROW
LEVEL SECURITY` (D-07-5 / D-08-1) and fail closed when `app.current_user_id` is
unset, so a normal Grafana connection sees **zero rows**. The ops dashboards
are operator-only and must read across tenants — provision a read-only role
with `BYPASSRLS`:

```sql
-- run as the database superuser, ONCE per environment
CREATE ROLE grafana_ro LOGIN PASSWORD '<strong-password>' NOSUPERUSER BYPASSRLS;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON personas, conversations, turn_logs, memory_chunks,
                  credit_transactions TO grafana_ro;
```

Do **not** expose this role to the web app or any tenant-facing surface — it
intentionally bypasses RLS for operator visibility.

## Setup — Grafana datasource

Add a Postgres datasource in Grafana pointing at the same DB the API uses
(`persona`), with `grafana_ro` as the user. Set the datasource **uid** to
match the dashboards' `${DS_POSTGRES}` placeholder (or edit the dashboards to
your uid).

```text
Name:       persona-pg
Host:       <api-host>:5432   (or :5436 locally)
Database:   persona
User:       grafana_ro
SSL mode:   require            (in production)
```

## Importing the dashboards

```text
Grafana → Dashboards → New → Import → Upload JSON file
```

Upload each file in turn; bind it to the `persona-pg` datasource when prompted.
Both dashboards are tagged `open-persona` for discovery.

## What's in each dashboard

### §6.1 — Per-persona usage

- **Conversations per persona (30d)** — `conversations` GROUP BY `persona_id`.
- **Average turns per conversation (over time)** — derived from `turn_logs`
  (turns per `conversation_id`, day-bucketed).
- **Episodic chunk count per persona** — `memory_chunks` WHERE `kind='episodic'`
  AND `superseded_by IS NULL` GROUP BY `persona_id`. Spec-11 soak measurement
  hook for the eviction decision (D-11-4).
- **Compaction events per persona** — derived as `⌊compacted_up_to / 10⌋`
  (`compact_every=10`, spec 05). Approximation, not an event counter.

### §6.2 — Routing health

- **Tier distribution (frontier/mid/small)** — stacked-area from
  `turn_logs.tier_used`. Makes the architecture's tier-routing thesis visible.
- **Cost per conversation** — `SUM(turn_logs.cost_cents) / 100.0` per
  conversation. Estimate; not billing.
- **Tool calls per turn** — histogram of `turn_logs.tool_calls`.
- **Skill activations per day** — `COUNT(*) WHERE skill_used IS NOT NULL`,
  grouped by day + skill.

## §6.3 — System health (DOCUMENTED, post-September)

Per D-11-5, the system-health dashboard from spec §6.3 is **deferred**. It
prescribes:

- Request latency p50 / p95 / p99 **per endpoint**
- Error rate **per endpoint**
- Rate-limit rejections per minute
- Provider availability — successful vs failed model calls per provider

These need **request-level telemetry that no table captures**: `turn_logs` has
no `endpoint` / HTTP-status / error dimension; `latency_ms` is per-*turn*
(model latency), not per-*endpoint* p99; provider availability isn't logged.
Implementing §6.3 requires adding request-telemetry middleware (or shipping
metrics to Prometheus/OpenTelemetry) — out of scope for a single-operator v0.1
demo and flagged as **additive scope**, not silently folded in.

A *partial* turn-level dashboard (model latency by provider, turn-level error
counts) is derivable from `turn_logs`; ship it as a follow-up if useful, but
note it is NOT the spec's endpoint-level system-health view.

## Versioning

The dashboards' `version: 1` reflects the initial commit. Bump when editing
the JSON in-repo; re-importing in Grafana keeps the runtime version separate.
