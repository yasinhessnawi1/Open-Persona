# persona-web

> Next.js web app for Open Persona.

**Status:** Private · In Development

## What it is

The browser front-end for Open Persona. Next.js 16 (App Router) +
TypeScript (strict) + Tailwind v4 + shadcn/ui, Clerk auth, an
OpenAPI-generated client against `persona-api`, and Playwright + Vitest +
Biome for the verification surface. A thin client; all business logic
lives in the API. SSE streams (chat + agentic-run timeline) are consumed
via `fetch` + `ReadableStream` with hand-mirrored event shapes in
`src/lib/sse-types.ts` because OpenAPI cannot model server-sent events.

Pages:

- **Landing**: public marketing page; auth-aware CTAs.
- **Auth**: Clerk sign-in/up/out; the `(app)` route group is protected.
- **Personas**: list / detail; authoring flow (one-sentence brief →
  frontier draft → structured form ⇄ lazy Monaco YAML → save) + edit.
- **Chat**: streaming SSE chat with visible identity header, collapsible
  tool-call cards, per-turn tier badge, file + image attachments.
- **Run viewer**: agentic-run timeline over SSE (catch-up + reconcile-on-
  drop), inline ask-user, Markdown final answer, cancel.
- **Settings**: credit balance + per-turn usage; theme, tier-badge
  visibility, language (pseudo-locale) toggles; real conversations list.

Responsive (usable at 375px), dark-mode-default, fully i18n via
`next-intl`. The package is a **standalone Node project**; it is **not**
part of the `uv` workspace (that's `core` / `runtime` / `api` / `voice`);
`uv sync` does nothing here.

## Install

`persona-web` is **not yet open source** and **not** published. Development
install:

```bash
git clone <internal-repo-url>
cd open-persona/packages/web
pnpm install
cp .env.example .env.local       # NEXT_PUBLIC_API_BASE_URL + Clerk keys
```

Prerequisites: Node ≥ 20.9 (tested on 20.19), pnpm 10.x, a running
`persona-api` on `http://127.0.0.1:8000` for live data.

## Run

```bash
pnpm dev                          # http://localhost:3000 (Turbopack)
pnpm build                        # production build (catches RSC errors tsc misses)
pnpm start                        # serve the production build
```

To run the full stack locally: `docker start persona-pg` →
`cd ../api && bash run-local.sh` → `pnpm dev`.

## Test

```bash
pnpm typecheck                    # tsc --noEmit (strict, no any)
pnpm lint                         # Biome (format + lint, React-hooks + Next rules)
pnpm format                       # Biome auto-format
pnpm test                         # Vitest + React Testing Library
pnpm test:e2e                     # Playwright against a real browser + live API
```

"Done" for a UI change = `typecheck` + `lint` + `build` + `test` clean
**and** the feature works in a running browser. The E2E harness (under
`e2e/`) fetches a Clerk testing token, signs up a `+clerk_test` user
(OTP `424242`), saves `storageState`, and runs specs authed.

## Architecture role

`persona-web` is layer 1 of the Open Persona stack: the surface users
actually touch. It calls `persona-api` over REST (committed generated
client from `openapi.json`; never hand-written `fetch`) and consumes SSE
streams for chat and run viewers. State: TanStack Query for server state,
React context for UI state, no global store. The package does not link to
`persona-core`, `persona-runtime`, or `persona-voice` directly; every
backend concern crosses the API boundary.

## Contribute

This package is currently private and **not** accepting external
contributions yet. See `AGENTS.md` for the project's house rules on the
Next.js 16 surface (this is not the Next.js your training data knows;
read `node_modules/next/dist/docs/` before writing code).
