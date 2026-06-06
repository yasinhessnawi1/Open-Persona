/**
 * Spec F2 T34 — Criterion-#11 evidence package.
 *
 * The human-review boundary for the F2 rebuild. Six rebuilt screens, four
 * evidence panels per screen, twenty-four panels total. The reviewer's job:
 * walk each section and confirm the F2 rebuild closed what it set out to
 * close — the `0.65rem literal` legacy, the `bg-primary/10` D-F1-5 violation,
 * the hand-rolled `mx-auto max-w-…px-…py-…` page-body pattern.
 *
 * Per-screen panel structure:
 *   1. **Composition** — the F2 layout (PageBody + PageHeader / IdentityHeader
 *      / Stack / Section / Grid + retokenised typography).
 *   2. **Closures** — the named scaffold violations that were closed, with
 *      the file:line of the original and the F2 token replacement.
 *   3. **Alternate state** — empty / loading / error variant when applicable.
 *   4. **Dark mode** — the same composition under `.dark` (D-F1-6 proof).
 *
 * Live components compose F1 fixture personas (Astrid / Kai / Maren) so the
 * §4 individuality is demonstrated in the F2 rebuild surface.
 */
import Link from "next/link";
import {
  Grid,
  PageBody,
  PageHeader,
  Section,
  Stack,
} from "@/components/layout";
import { EmptyState } from "@/components/patterns/empty-state";
import { ErrorState } from "@/components/patterns/error-state";
import {
  SkeletonAvatar,
  SkeletonBlock,
  SkeletonLine,
} from "@/components/patterns/loading";
import { PersonaAvatar } from "@/components/persona/persona-avatar";
import { PersonaCard } from "@/components/persona/persona-card";
import { PersonaIdentityHeader } from "@/components/persona/persona-identity-header";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { ASTRID, KAI, MAREN, type ReferencePersona } from "../_fixtures";

const ASTRID_HEADER = {
  id: ASTRID.id,
  name: ASTRID.name,
  role: ASTRID.role,
  constraint: "Never give binding legal advice.",
};

function Panel({
  title,
  caption,
  dark,
  children,
}: {
  title: string;
  caption?: string;
  dark?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Card
      className={
        dark
          ? "dark gap-3 border-border/60 bg-background p-5"
          : "gap-3 border-border/60 p-5"
      }
      data-slot="review-panel"
    >
      <header>
        <p className="type-caption font-mono text-muted-foreground uppercase">
          {title}
        </p>
        {caption ? (
          <p className="type-ui mt-1 text-muted-foreground">{caption}</p>
        ) : null}
      </header>
      <div className="rounded-md border bg-card p-4">{children}</div>
    </Card>
  );
}

function Closures({ items }: { items: { before: string; after: string }[] }) {
  return (
    <ul className="type-ui flex flex-col gap-2">
      {items.map((it) => (
        <li key={it.before} className="flex flex-col gap-0.5">
          <code className="type-caption font-mono text-destructive">
            − {it.before}
          </code>
          <code className="type-caption font-mono text-tier-small">
            + {it.after}
          </code>
        </li>
      ))}
    </ul>
  );
}

function CardListPreview({ personas }: { personas: ReferencePersona[] }) {
  return (
    <Stack gap={2}>
      {personas.map((p) => (
        <PersonaCard key={p.id} persona={p} />
      ))}
    </Stack>
  );
}

function ScreenSection({
  id,
  title,
  task,
  byline,
  livePath,
  children,
}: {
  id: string;
  title: string;
  task: string;
  byline: string;
  livePath: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="space-y-4" data-slot="review-screen">
      <header className="border-b border-border/60 pb-3">
        <p className="type-caption font-mono text-muted-foreground uppercase">
          {task}
        </p>
        <h2 className="type-display mt-1">{title}</h2>
        <p className="type-body mt-1 text-muted-foreground">{byline}</p>
        <Link
          href={livePath}
          className="type-ui mt-2 inline-block text-primary hover:underline"
        >
          Live: {livePath} →
        </Link>
      </header>
      <Grid cols={{ base: 1, md: 2 }} gap={4}>
        {children}
      </Grid>
    </section>
  );
}

export default function ReviewF2Page() {
  return (
    <PageBody width="wide">
      <PageHeader
        title="F2 review"
        subtitle="Criterion-#11 evidence — six rebuilt screens, four panels each, the human-review boundary for the F2 rebuild."
      />

      <Stack gap={4} className="mb-10">
        <Card className="gap-3 border-primary/30 p-5">
          <h2 className="type-heading">What this page proves</h2>
          <p className="type-body text-muted-foreground">
            Each section below walks one rebuilt screen with four evidence
            panels: the F2 composition, the closed scaffold violations (named
            with file:line + token replacement), an alternate state (empty /
            loading / error when applicable), and the dark mode preview (D-F1-6
            proof). The plumbing — fetch, hooks, normalisers, state machines —
            was preserved verbatim per the per-route{" "}
            <code className="font-mono">§*.plumbing</code> DO-NOT-TOUCH
            inventories in <code className="font-mono">audit.md</code>.
          </p>
        </Card>

        <nav aria-label="F2 screen index">
          <Grid cols={{ base: 1, sm: 2, lg: 3 }} gap={2}>
            {[
              { id: "t26-chat", label: "T26 Chat" },
              { id: "t27-personas-list", label: "T27 Persona list" },
              { id: "t28-persona-detail", label: "T28 Persona detail" },
              { id: "t29-authoring", label: "T29 Authoring" },
              { id: "t30-run-viewer", label: "T30 Run viewer" },
              { id: "t31-settings", label: "T31 Settings" },
            ].map((entry) => (
              <Link
                key={entry.id}
                href={`#${entry.id}`}
                className="type-ui rounded-md border bg-card px-3 py-2 text-muted-foreground transition-colors hover:border-primary/30 hover:text-foreground"
              >
                {entry.label}
              </Link>
            ))}
          </Grid>
        </nav>
      </Stack>

      <Stack gap={8} className="[&>section]:pt-4">
        {/* ─────────────── T26 Chat ─────────────── */}
        <ScreenSection
          id="t26-chat"
          task="T26 · D-F2-15 · live markdown"
          title="Chat"
          byline="PersonaIdentityHeader replaces the bg-primary/10 D-F1-5 violation; interleaved tool layout walks events[] in stream order; markdown renders live as tokens arrive (post-2026-06-06 amendment); thinking + tool-running indicators bridge activity-state pauses."
          livePath="/chat"
        >
          <Panel
            title="Composition — light"
            caption="<PersonaIdentityHeader> with the identity-coloured underline + constraint cue"
          >
            <PersonaIdentityHeader
              persona={ASTRID_HEADER}
              size="md"
              showConstraints
            />
          </Panel>

          <Panel title="Closures">
            <Closures
              items={[
                {
                  before:
                    "message-bubble.tsx: bg-primary/10 text-primary avatar fallback",
                  after: "<PersonaAvatar> identity-coloured fill",
                },
                {
                  before:
                    "stacked tool layout — cards clumping above text on render",
                  after:
                    "D-F2-15 interleaved layout — events[] walked in stream order",
                },
                {
                  before: "raw markdown until end-of-stream",
                  after:
                    "live <Markdown> re-render per chunk (token-granularity)",
                },
                {
                  before: "ThinkingIndicator dots-only with hidden aria-label",
                  after:
                    "italic .type-ui label + py-1.5 + size-2 dots + 0/200/400ms wave",
                },
              ]}
            />
          </Panel>

          <Panel
            title="Activity-state matrix"
            caption="The four states a chat message can sit in"
          >
            <Stack gap={2}>
              <p className="type-ui">
                <strong>Thinking</strong> — italic “Astrid is thinking…” +
                pulsing dots
              </p>
              <p className="type-ui">
                <strong>Streaming text</strong> — Markdown + vermilion caret
                below
              </p>
              <p className="type-ui">
                <strong>Tool running</strong> — italic “Astrid is using
                web_search…”
              </p>
              <p className="type-ui">
                <strong>Done</strong> — TierBadge below message body
              </p>
            </Stack>
          </Panel>

          <Panel title="Composition — dark" dark>
            <PersonaIdentityHeader
              persona={ASTRID_HEADER}
              size="md"
              showConstraints
            />
          </Panel>
        </ScreenSection>

        {/* ─────────────── T27 Persona list ─────────────── */}
        <ScreenSection
          id="t27-personas-list"
          task="T27 · §4 individuality proof"
          title="Persona list"
          byline="PageBody + PageHeader + Grid of PersonaCard. The §4 multi-persona individuality proof made operational — Astrid + Kai + Maren read as three distinct identity colours at a glance."
          livePath="/personas"
        >
          <Panel
            title="Composition — light"
            caption="Three distinct identity colours, one coherent product"
          >
            <CardListPreview personas={[ASTRID, KAI, MAREN]} />
          </Panel>

          <Panel title="Closures">
            <Closures
              items={[
                {
                  before:
                    "personas/persona-card.tsx (scaffold): bg-primary/10 text-primary fallback",
                  after:
                    "persona/persona-card.tsx (F2): <PersonaAvatar> with per-persona OKLCH",
                },
                {
                  before: "mx-auto max-w-4xl px-4 py-8 sm:px-6 (hand-rolled)",
                  after: "<PageBody>",
                },
                {
                  before: "inline empty-state markup with hard-coded grid-rule",
                  after: "<EmptyState icon title description action /> via T22",
                },
              ]}
            />
          </Panel>

          <Panel
            title="Empty state"
            caption="EmptyState pattern — inviting, never apologetic"
          >
            <EmptyState
              title="No personas yet"
              description="Describe one in a sentence and the authoring flow drafts the rest."
              action={
                <Link
                  href="/personas/new"
                  className="type-ui rounded-md border border-primary px-3 py-1 text-primary hover:bg-primary/5"
                >
                  New persona
                </Link>
              }
            />
          </Panel>

          <Panel title="Composition — dark" dark>
            <CardListPreview personas={[ASTRID, KAI, MAREN]} />
          </Panel>
        </ScreenSection>

        {/* ─────────────── T28 Persona detail ─────────────── */}
        <ScreenSection
          id="t28-persona-detail"
          task="T28 · 0.65rem literal closure"
          title="Persona detail"
          byline="PersonaIdentityHeader size='lg' + Stack of Section-card blocks (background, constraints, self-facts, worldview, tools, skills). 0.65rem literal epistemic badge closed via .type-caption."
          livePath="/personas/{id}"
        >
          <Panel title="Identity header — light">
            <PersonaIdentityHeader persona={ASTRID_HEADER} size="lg" />
          </Panel>

          <Panel title="Closures">
            <Closures
              items={[
                {
                  before:
                    "page.tsx:47 — bg-primary/10 font-heading text-primary fallback",
                  after: "<PersonaIdentityHeader size='lg'> (D-F1-5 composite)",
                },
                {
                  before:
                    "page.tsx:136 — font-mono 0.65rem literal uppercase epistemic badge",
                  after: ".type-caption font-mono uppercase (F1 token)",
                },
                {
                  before: "inline <Section>/<Empty>/<Chips> subcomponents",
                  after:
                    "T20 <Section heading> + muted .type-body + retokenised <Badge>",
                },
              ]}
            />
          </Panel>

          <Panel
            title="Epistemic badge — closed"
            caption="The 0.65rem legacy now resolves through F1 --text-caption-*"
          >
            <div className="flex items-baseline gap-2">
              <span className="type-body">
                Tenants in Norway have strong protections.
              </span>
              <Badge
                variant="outline"
                className="type-caption font-mono uppercase"
              >
                fact
              </Badge>
            </div>
          </Panel>

          <Panel title="Identity header — dark" dark>
            <PersonaIdentityHeader persona={ASTRID_HEADER} size="lg" />
          </Panel>
        </ScreenSection>

        {/* ─────────────── T29 Authoring ─────────────── */}
        <ScreenSection
          id="t29-authoring"
          task="T29 · AuthorWizard retokenise"
          title="Authoring"
          byline="Three phases (describe / loading / review) retokenised: byline → .type-caption, title → .type-display, body → .type-body. Shadcn Skeleton replaced with T21 SkeletonLine. useAuthor + 3-round refine cap + PersonaEditor preserved."
          livePath="/personas/new"
        >
          <Panel
            title="Describe phase composition"
            caption="<Stack> + retokenised byline + Fraunces hero title"
          >
            <Stack gap={3}>
              <p className="type-caption font-mono text-muted-foreground uppercase">
                New persona
              </p>
              <h3 className="type-display">Describe your persona</h3>
              <p className="type-body text-muted-foreground">
                One or two sentences. The frontier model drafts the identity,
                worldview, and constraints — you refine from there.
              </p>
            </Stack>
          </Panel>

          <Panel title="Closures">
            <Closures
              items={[
                {
                  before: "font-mono text-xs tracking-wide uppercase",
                  after: ".type-caption font-mono uppercase",
                },
                {
                  before: "font-heading text-3xl font-semibold tracking-tight",
                  after: ".type-display (Fraunces token)",
                },
                {
                  before: "shadcn <Skeleton> (uncontrolled animation)",
                  after: "<SkeletonLine> (--motion-duration-* resolved)",
                },
                {
                  before:
                    "inline error: text-sm text-destructive (no aria-role)",
                  after:
                    ".type-ui text-destructive with role='alert' (assertive)",
                },
              ]}
            />
          </Panel>

          <Panel
            title="Loading state"
            caption="The designed 10-30s frontier-call surface"
          >
            <Stack gap={3}>
              <p className="type-heading">Authoring your persona</p>
              <p className="type-ui text-muted-foreground">
                Shaping identity and voice…
              </p>
              <Card className="gap-3 p-5">
                <SkeletonLine className="w-24" />
                <SkeletonLine className="w-3/4" />
                <SkeletonLine className="w-1/2" />
              </Card>
            </Stack>
          </Panel>

          <Panel title="Describe phase — dark" dark>
            <Stack gap={3}>
              <p className="type-caption font-mono text-muted-foreground uppercase">
                New persona
              </p>
              <h3 className="type-display">Describe your persona</h3>
              <p className="type-body text-muted-foreground">
                One or two sentences. The frontier model drafts the identity,
                worldview, and constraints — you refine from there.
              </p>
            </Stack>
          </Panel>
        </ScreenSection>

        {/* ─────────────── T30 Run viewer ─────────────── */}
        <ScreenSection
          id="t30-run-viewer"
          task="T30 · runs retokenise"
          title="Run viewer"
          byline="PersonaAvatar size='lg' + .type-heading task title + retokenised StepCard / RunStatusBadge / RunTimeline / AskUserPrompt. Every 0.65rem literal closed to .type-caption. useRun + runViewFromEvents + sse-types preserved."
          livePath="/runs/{id}"
        >
          <Panel
            title="Header composition"
            caption="Avatar + .type-caption byline + .type-heading task"
          >
            <div className="flex items-start gap-4">
              <PersonaAvatar persona={ASTRID} size="lg" />
              <div className="min-w-0 flex-1">
                <p className="type-caption font-mono text-muted-foreground uppercase">
                  Astrid · Run
                </p>
                <h3 className="type-heading mt-1">
                  Summarise the deposit-return rules under husleieloven §5-1.
                </h3>
              </div>
            </div>
          </Panel>

          <Panel title="Closures">
            <Closures
              items={[
                {
                  before:
                    "5× font-mono 0.65rem literal tracking-wide uppercase across run components",
                  after: ".type-caption font-mono uppercase (5× closed)",
                },
                {
                  before: "<Avatar><AvatarFallback className='bg-primary/10'>",
                  after: "<PersonaAvatar size='lg'>",
                },
                {
                  before: "<div className='flex flex-col gap-5'>",
                  after: "<Stack gap={5}>",
                },
                {
                  before: "step error: text-sm text-destructive",
                  after: ".type-ui text-destructive with role='alert'",
                },
              ]}
            />
          </Panel>

          <Panel
            title="Loading skeleton"
            caption="Avatar + byline + 3 step skeletons"
          >
            <Stack gap={3}>
              <div className="flex items-start gap-4">
                <SkeletonAvatar size="lg" />
                <div className="min-w-0 flex-1 space-y-2">
                  <SkeletonLine className="w-24" />
                  <SkeletonLine className="w-2/3" />
                </div>
              </div>
              <Card className="p-4">
                <SkeletonBlock lines={3} />
              </Card>
            </Stack>
          </Panel>

          <Panel title="Header — dark" dark>
            <div className="flex items-start gap-4">
              <PersonaAvatar persona={ASTRID} size="lg" />
              <div className="min-w-0 flex-1">
                <p className="type-caption font-mono text-muted-foreground uppercase">
                  Astrid · Run
                </p>
                <h3 className="type-heading mt-1">
                  Summarise the deposit-return rules under husleieloven §5-1.
                </h3>
              </div>
            </div>
          </Panel>
        </ScreenSection>

        {/* ─────────────── T31 Settings ─────────────── */}
        <ScreenSection
          id="t31-settings"
          task="T31 · settings retokenise"
          title="Settings"
          byline="PageBody + PageHeader + Stack of cards (account / credits / preferences / usage). Credit balance in .type-display. Credits-exhausted (balance===0) surfaces via T22 ErrorState status=402."
          livePath="/settings"
        >
          <Panel
            title="Credits card"
            caption="Fraunces hero balance + retokenised section label"
          >
            <Card className="gap-2 p-5">
              <h3 className="type-caption font-mono text-muted-foreground uppercase">
                Credits
              </h3>
              <p className="type-display tabular-nums">12,400</p>
              <p className="type-caption text-muted-foreground">
                Credits are spent per turn, by model tier.
              </p>
            </Card>
          </Panel>

          <Panel title="Closures">
            <Closures
              items={[
                {
                  before:
                    "h1 font-heading text-3xl font-semibold tracking-tight",
                  after: "<PageHeader title> with .type-heading via T20",
                },
                {
                  before:
                    "section h2 font-heading text-sm tracking-wide uppercase",
                  after: ".type-caption font-mono uppercase",
                },
                {
                  before:
                    "balance: font-heading text-3xl font-semibold tabular-nums",
                  after: ".type-display tabular-nums",
                },
                {
                  before: "Row label text-sm + hint text-xs",
                  after: ".type-body font-medium + .type-caption",
                },
              ]}
            />
          </Panel>

          <Panel
            title="Credits exhausted — 402 ErrorState"
            caption="balance===0 surfaces via T22 with ring-primary/40 tone"
          >
            <ErrorState
              status={402}
              copy={{
                title: "Credits exhausted",
                description:
                  "Top up your balance or contact support to keep using premium tiers.",
              }}
            />
          </Panel>

          <Panel title="Credits card — dark" dark>
            <Card className="gap-2 p-5">
              <h3 className="type-caption font-mono text-muted-foreground uppercase">
                Credits
              </h3>
              <p className="type-display tabular-nums">12,400</p>
              <p className="type-caption text-muted-foreground">
                Credits are spent per turn, by model tier.
              </p>
            </Card>
          </Panel>
        </ScreenSection>
      </Stack>

      <Section heading="What's preserved (per audit.md)">
        <Card className="gap-3 p-5">
          <p className="type-body text-muted-foreground">
            Every per-route <code className="font-mono">§*.plumbing</code>{" "}
            DO-NOT-TOUCH inventory was honoured. The four hard read-only
            invariants from the F2 kickoff:
          </p>
          <ul className="type-body flex list-disc flex-col gap-1 pl-6 text-muted-foreground">
            <li>
              <code className="font-mono">src/lib/run.ts</code> polymorphic
              steps normaliser — read-only.
            </li>
            <li>
              <code className="font-mono">useRun</code> /{" "}
              <code className="font-mono">useChat</code> /{" "}
              <code className="font-mono">useAuthor</code> hooks — untouched.
            </li>
            <li>
              <code className="font-mono">serverApi()</code> server-side fetch
              wiring — untouched.
            </li>
            <li>
              <code className="font-mono">parsePersonaYaml</code> /{" "}
              <code className="font-mono">yamlToDoc</code> /{" "}
              <code className="font-mono">docToYaml</code> family — untouched.
            </li>
          </ul>
        </Card>
      </Section>
    </PageBody>
  );
}
