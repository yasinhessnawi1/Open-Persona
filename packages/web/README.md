# persona-web

> The Next.js 16 web app for Open Persona — persona authoring, streaming chat, agentic runs, and voice, in two editions.

`persona-web` is **layer 1** of the [Open Persona](../../README.md) stack: the
surface users actually touch. It is a thin client — all business logic lives in
[`persona-api`](../api/README.md) — talking REST + SSE over an OpenAPI-generated
client, with every auth touch isolated behind a swappable `@/auth` seam.

> **Note:** this is **not** the Next.js your training data knows. The app pins
> Next.js 16 (App Router, Turbopack) with breaking changes from prior versions.
> See [`AGENTS.md`](AGENTS.md) — read `node_modules/next/dist/docs/` before
> writing code.

---

## What it is / where it fits

The browser front-end: Next.js 16 (App Router) + TypeScript (strict) +
Tailwind v4 + shadcn/ui, an OpenAPI-generated client against `persona-api`, and
Biome + Vitest + Playwright as the verification surface. SSE streams (chat +
agentic-run timelines) are consumed via `fetch` + `ReadableStream` with
hand-mirrored event shapes (OpenAPI cannot model server-sent events). State is
server-state-first; no global store.

It ships in **two editions**, selected at **build time** by `PERSONA_EDITION`:

- **community** (default) — a **Clerk-free** build: no sign-in wall, a fixed
  local owner, middleware is a pass-through. Pairs with the community API
  (SQLite + Chroma, no auth, no credits) for a clone-and-run local self-host.
- **cloud** — the owner's commercial hosting: Clerk auth (`ClerkProvider`,
  sign-in/up, `<UserButton>`), JWT-templated bearer tokens to the API, and the
  credits dashboard.

The edition is a **build-time module swap**: every Clerk touch is isolated
behind a `@/auth` façade (`@/auth`, `@/auth/server`, `@/auth/provider`,
`@/auth/middleware`) with `*.cloud.*` / `*.community.*` variants that share one
`types.ts` surface. `turbopack.resolveAlias` (in `next.config.ts`), keyed on
`PERSONA_EDITION`, points each import at the edition's variant — so a community
build never pulls `@clerk/*` into the bundle (enforced by CI gates +
`no-restricted-imports`).

## Features

- **Persona authoring** — one-sentence brief → frontier draft → structured form
  ⇄ lazy Monaco YAML editor → save, plus edit and a files manager.
- **Streaming chat** — SSE chat with a visible identity header, collapsible
  tool-call cards, per-turn tier badges, file + image attachments, and a
  right-panel artifact renderer covering 10 formats (markdown / code / PDF /
  image / CSV / JSON / HTML / Mermaid / Graphviz / plaintext, rendered↔raw
  toggle).
- **Agentic run viewer** — run timeline over SSE (catch-up + reconcile-on-drop),
  inline ask-user, Markdown final answer, cancel.
- **Voice** — a browser voice client (LiveKit `livekit-client`) wired to the
  `/v1/voice/token` flow; a voice orb surface (V6, in flight).
- **Settings** — credit balance + per-turn usage, theme, tier-badge
  visibility, language toggle, conversations list.
- **Notifications + consent** — one app-wide notification façade (`useNotify`)
  over the toast layer, plus a persistent bell center (client-side feed); and
  one consent dialog (`useConfirm`) for every confirmation — no native browser
  dialogs.
- **Responsive + i18n** — usable at 375px, dark-mode-default, fully
  internationalized via `next-intl`.

> The web v1 production redesign is complete: an identity-spine `.v-*` design
> system over F1 tokens (no literal colours), a rebuilt sidebar + ⌘K palette,
> and the three signature moments (per-turn tier, typed-memory recall, presence).

## Install / run

`persona-web` is a **standalone Node project** — it is **not** part of the `uv`
workspace (`uv sync` does nothing here). Use `pnpm`.

```bash
cd packages/web
pnpm install
cp .env.example .env.local        # NEXT_PUBLIC_API_BASE_URL (+ Clerk keys for cloud)
```

Prerequisites: Node ≥ 20.9, pnpm 10.x, and a running `persona-api` for live
data.

```bash
# community (default) — Clerk-free, pairs with the zero-infra community API
pnpm dev                          # http://localhost:3000 (Turbopack)
pnpm build                        # community build (omits Clerk)

# cloud — Clerk auth + credits
pnpm dev:cloud                    # PERSONA_EDITION=cloud next dev
pnpm build:cloud                  # PERSONA_EDITION=cloud next build
pnpm start                        # serve a production build
```

Full stack locally: run the API (`cd ../api && bash run-local.sh`), then
`pnpm dev`.

### Verify

```bash
pnpm typecheck                    # tsc --noEmit (strict)
pnpm lint                         # Biome (format + lint)
pnpm test                         # Vitest + React Testing Library
pnpm test:e2e                     # Playwright against a real browser + live API
pnpm check:clerk-free             # community build pulls no @clerk/* into src
pnpm gen:api                      # regenerate the OpenAPI client from openapi.json
```

"Done" for a UI change = `typecheck` + `lint` + `build` + `test` clean **and**
the feature works in a running browser.

## Usage / key surfaces

App structure (App Router):

- `(app)` — the authenticated app group: `chat`, `personas` (+ `new`, `[id]`,
  edit, files), `conversations`, `runs`, `settings`. In community the group has
  no auth wall.
- `(auth)` — Clerk sign-in / sign-up / reset-password (cloud); community
  variants redirect home.
- Landing page — public, auth-aware CTAs.

**API access.** Every backend call goes through the committed OpenAPI-generated
client (`src/lib`) — never hand-written `fetch`. The bearer token comes from the
`@/auth` seam: a Clerk JWT-template token in cloud, `null` in community. SSE
streams (chat, runs) are read via `ReadableStream` with the event shapes
mirrored in `src/lib`.

## Architecture (brief)

```
browser ──┐
          │  Next.js 16 (App Router, Turbopack)
          │  @/auth seam ─ build-time swap (turbopack.resolveAlias)
          │     community → no-auth stub (Clerk-free bundle)
          │     cloud     → Clerk
          ▼
   persona-api  (REST + SSE, OpenAPI)
```

The web app never links `persona-core`, `persona-runtime`, or `persona-voice`
directly — every backend concern crosses the API boundary.

## License

`persona-web` is licensed under **PolyForm Noncommercial 1.0.0** — see
[LICENSE](LICENSE) (`package.json` declares `"SEE LICENSE IN LICENSE"`). It is
**source-available, not OSI "open source"**: you may read, modify, and
self-host it for personal, research, evaluation, educational, and other
**noncommercial** use, but **commercial use requires a separate license** from
the rights holder. The engine packages
(`persona-core` / `persona-runtime` / `persona-voice`) are separately
**MIT**-licensed and free for any use.

## Links

- [Open Persona root README](../../README.md)
- [`persona-core`](../core/README.md) · [`persona-runtime`](../runtime/README.md) · [`persona-voice`](../voice/README.md) · [`persona-api`](../api/README.md)
- [AGENTS.md](AGENTS.md) — Next.js 16 house rules · [CHANGELOG](CHANGELOG.md)
