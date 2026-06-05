/**
 * Spec F1 T10 — Reference composition: agentic run unfolding.
 *
 * The instrument half of the north star: the system shows its work. A step
 * timeline mid-run with a tool-call card, a partial step, and a "thinking"
 * indicator. Reuses the visual logic of run-timeline.tsx / step-card.tsx
 * without re-importing them (fixture-fed, isolated).
 */
import { PersonaAvatar } from "@/components/persona/persona-avatar";
import { personaIdentityStyle } from "@/lib/persona-identity";
import { ASTRID } from "../_fixtures";

const IDENTITY_OKLCH =
  "oklch(var(--identity-l) var(--identity-c) var(--identity-h))";

interface Step {
  n: number;
  type: "tool_call" | "reasoning" | "final";
  title: string;
  detail?: string;
  tier?: "small" | "mid" | "frontier";
  state: "done" | "running";
}

const STEPS: Step[] = [
  {
    n: 1,
    type: "reasoning",
    title: "Plan",
    detail:
      "User needs draft response to landlord challenging deposit deductions. I'll search husleieloven §3-5 case law, then draft.",
    tier: "frontier",
    state: "done",
  },
  {
    n: 2,
    type: "tool_call",
    title: "web_search",
    detail: "husleieloven §3-5 normal slitasje røyking depositum",
    tier: "mid",
    state: "done",
  },
  {
    n: 3,
    type: "tool_call",
    title: "web_fetch",
    detail: "husleietvistutvalget.no · sak 2024-178",
    tier: "small",
    state: "done",
  },
  {
    n: 4,
    type: "reasoning",
    title: "Drafting response",
    detail: "Composing letter citing case 2024-178 and §3-5.",
    tier: "frontier",
    state: "running",
  },
];

function StepDot({ step }: { step: Step }) {
  if (step.state === "running") {
    return (
      <span
        style={{ background: IDENTITY_OKLCH }}
        className="size-3 animate-pulse rounded-full"
        aria-hidden
      />
    );
  }
  return (
    <span
      className="bg-foreground/30 size-3 rounded-full ring-2 ring-background"
      aria-hidden
    />
  );
}

function TierBadge({ tier }: { tier: NonNullable<Step["tier"]> }) {
  const classes = {
    small: "border-tier-small/50 text-tier-small",
    mid: "border-tier-mid/50 text-tier-mid",
    frontier: "border-tier-frontier/40 text-tier-frontier",
  }[tier];
  return (
    <span
      className={`type-caption inline-flex w-fit items-center rounded border px-1.5 py-0.5 ${classes}`}
    >
      {tier}
    </span>
  );
}

export default function RunReferencePage() {
  return (
    <div className="space-y-10">
      <header className="space-y-2">
        <p className="type-caption text-muted-foreground">T10 · §11.6</p>
        <h1 className="type-display">Agentic run — unfolding</h1>
        <p className="type-body text-muted-foreground max-w-prose">
          The instrument half of the north star. Astrid researching, then
          drafting. Each step shows the system's work without becoming a
          dashboard.
        </p>
      </header>

      <div
        style={personaIdentityStyle(ASTRID)}
        className="border-border bg-card space-y-6 rounded-lg border p-6"
      >
        <header className="flex items-start justify-between gap-4 border-b border-border pb-4">
          <div className="flex items-start gap-4">
            <PersonaAvatar persona={ASTRID} size="md" />
            <div>
              <p
                style={{
                  borderBottomColor: IDENTITY_OKLCH,
                  borderBottomWidth: "1px",
                  borderBottomStyle: "solid",
                }}
                className="type-heading inline-block"
              >
                {ASTRID.name}
              </p>
              <p className="type-ui text-muted-foreground">
                drafting a response to landlord deposit deductions
              </p>
            </div>
          </div>
          <span className="type-caption border-tier-mid/50 text-tier-mid inline-flex items-center rounded border px-1.5 py-0.5">
            running · step 4 of ~5
          </span>
        </header>

        <ol className="space-y-3">
          {STEPS.map((step) => (
            <li
              key={step.n}
              className="border-border bg-background flex items-start gap-4 rounded-lg border p-4"
            >
              <div className="mt-1.5 flex flex-col items-center">
                <StepDot step={step} />
                {step.n < STEPS.length ? (
                  <span className="bg-border mt-1.5 h-12 w-px" aria-hidden />
                ) : null}
              </div>
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex items-center gap-3">
                  <span className="type-caption text-muted-foreground">
                    Step {step.n}
                  </span>
                  <span className="type-caption text-foreground/70">
                    {step.type.replace("_", " ")}
                  </span>
                  {step.tier ? <TierBadge tier={step.tier} /> : null}
                </div>
                <p className="type-body text-foreground font-medium">
                  {step.title}
                </p>
                {step.detail ? (
                  <p className="type-ui text-muted-foreground">{step.detail}</p>
                ) : null}
                {step.state === "running" ? (
                  <p className="type-caption text-muted-foreground">
                    Thinking…
                  </p>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
