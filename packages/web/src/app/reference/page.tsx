/**
 * Spec F1 — Reference compositions landing.
 *
 * Simple index linking the six T07–T12 compositions + T05 swatch sheet + T16
 * evidence review. T16 will replace this with a fuller review page; for now
 * this is the developer-facing index that the Phase-5 cadence renders during
 * implementation.
 */
import Link from "next/link";

interface ReferenceEntry {
  href: string;
  title: string;
  blurb: string;
  task: string;
}

const ENTRIES: ReferenceEntry[] = [
  {
    href: "/reference/swatches",
    title: "Persona identity-colour swatches",
    blurb:
      "The 12 curated hues + the derivation function on 12 representative personas. X-F1-2 / D-F1-1 artifact.",
    task: "T05",
  },
  {
    href: "/reference/chat",
    title: "Chat with Astrid (light)",
    blurb:
      "Single-persona §4 proof: identity header + accent-not-wash composite (D-F1-5).",
    task: "T07",
  },
  {
    href: "/reference/personas",
    title: "Persona list — Astrid + Kai + Maren",
    blurb:
      "Multi-persona §4 proof: three distinct individuals in one coherent product.",
    task: "T08",
  },
  {
    href: "/reference/author",
    title: "Persona authoring moment",
    blurb:
      "Editorial drafting feel: form filled, clarifying-question card waiting.",
    task: "T09",
  },
  {
    href: "/reference/run",
    title: "Agentic run unfolding",
    blurb: "Instrument half of the north star: the system showing its work.",
    task: "T10",
  },
  {
    href: "/reference/empty",
    title: "Empty state",
    blurb: "UI voice surface — invites rather than apologises.",
    task: "T11",
  },
  {
    href: "/reference/chat-dark",
    title: "Chat (dark)",
    blurb:
      "Same composition as /reference/chat, dark mode. D-F1-6 warmth proof.",
    task: "T12",
  },
];

export default function ReferenceIndex() {
  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <p className="type-caption text-muted-foreground">Spec F1</p>
        <h1 className="type-display">Reference Compositions</h1>
        <p className="type-body text-muted-foreground max-w-prose">
          Fixture-fed static screens that prove the design language composes.
          Each entry below is a deliberate piece of evidence for one or more of
          the spec's twelve acceptance criteria. The agent stops at criterion #7
          — the human sign-off lives at{" "}
          <Link
            href="/reference/review"
            className="text-primary underline-offset-4 hover:underline"
          >
            /reference/review
          </Link>{" "}
          (T16, not yet built).
        </p>
      </header>
      <ul className="grid gap-4 sm:grid-cols-2">
        {ENTRIES.map((entry) => (
          <li key={entry.href}>
            <Link
              href={entry.href}
              className="group border-border bg-card hover:border-primary/40 block rounded-lg border p-5 transition-colors"
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="type-caption text-muted-foreground">
                  {entry.task}
                </span>
                <span className="text-muted-foreground group-hover:text-primary type-caption transition-colors">
                  →
                </span>
              </div>
              <p className="type-heading text-foreground mb-1">{entry.title}</p>
              <p className="type-body text-muted-foreground">{entry.blurb}</p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
