/**
 * Spec F1 T16 — Criterion-#7 evidence index for human design review.
 *
 * This page is the agent/human boundary: every artefact the agent prepared
 * is linked from here. The HUMAN decides whether the north-star tension
 * holds (criterion #7 — warm-but-precise, neither sterile nor fussy).
 *
 * The agent does NOT self-certify #7. T16's job is to organise the evidence,
 * articulate the design framing, and stop.
 */
import Link from "next/link";

interface EvidenceEntry {
  href: string;
  title: string;
  what: string;
  task: string;
}

const EVIDENCE: EvidenceEntry[] = [
  {
    href: "/reference/swatches",
    title: "Identity-colour swatches",
    what: "The curated 12-hue palette + the derivation function applied to 12 representative personas. Does the palette feel coordinated (a single language) and varied (12 distinct people)? Does any persona out-shout the brand vermilion?",
    task: "T05 · D-F1-1",
  },
  {
    href: "/reference/chat",
    title: "Chat — Astrid (light)",
    what: "The single-persona accent-not-wash composite (D-F1-5). Three small accents per persona — avatar, name underline, 2px message border. Is Astrid's presence felt without the UI shouting? Is the streaming caret a quiet identity signal or too loud?",
    task: "T07 · D-F1-5",
  },
  {
    href: "/reference/personas",
    title: "Persona list — Astrid + Kai + Maren",
    what: "THE §4 multi-persona test. Three distinct individuals in one coherent product. Look across the room — do they read as three different people, or as the same card with name tags? Does any persona dominate or recede?",
    task: "T08 · §4 proof",
  },
  {
    href: "/reference/author",
    title: "Persona authoring moment",
    what: "The editorial drafting feel — a draft persona shown in structured form with a clarifying question waiting. Does authoring feel like working in a considered tool, or like filling a configuration form?",
    task: "T09",
  },
  {
    href: "/reference/run",
    title: "Agentic run unfolding",
    what: "The instrument half of the north star — the system shows its work. Step timeline with a tool call and a thinking indicator. Does the run feel like a serious tool or a dashboard?",
    task: "T10",
  },
  {
    href: "/reference/empty",
    title: "Empty state",
    what: "UI voice surface. Microcopy that invites rather than apologises. Does the empty state feel warm and confident, or apologetic and bureaucratic?",
    task: "T11 · UI voice",
  },
  {
    href: "/reference/chat-dark",
    title: "Chat — Astrid (dark)",
    what: "D-F1-6 proof — same composition in the warm dark mode. Does the dark base keep the editorial warmth, or does it tip into generic dashboard cool? Does Astrid stay recognisably Astrid across modes?",
    task: "T12 · D-F1-6",
  },
];

export default function ReviewPage() {
  return (
    <div className="space-y-12">
      <header className="space-y-3">
        <p className="type-caption text-muted-foreground">T16 · criterion #7</p>
        <h1 className="type-display">Design review — Spec F1</h1>
        <p className="type-body text-muted-foreground max-w-prose">
          The agent has prepared the evidence below. Criterion #7 ("the
          reference compositions hold the north-star tension — warm but precise,
          neither sterile nor fussy") is a human design judgement, not an
          automated test. The agent does not self-certify.
        </p>
      </header>

      <section className="border-border bg-card space-y-4 rounded-lg border p-6">
        <h2 className="type-heading">How to look at this</h2>
        <ol className="type-body text-muted-foreground space-y-3 list-decimal pl-5 marker:text-foreground/40">
          <li>
            Open each composition below. Spend at least 30 seconds on each — the
            judgement is about <em>feel</em>, not just structure.
          </li>
          <li>
            For chat (light + dark) and persona list, look at them in both light
            and dark mode via your OS theme. The token swap is the only
            mechanism.
          </li>
          <li>
            Compare to the two failure modes:
            <ul className="mt-2 ml-4 list-disc space-y-1">
              <li>
                <strong className="text-foreground">Too sterile:</strong>
                generic AI dashboard, no warmth, no character. Persona is a
                "configure" tool.
              </li>
              <li>
                <strong className="text-foreground">Too fussy:</strong>
                over-styled, slow, precious. Persona is a coffee-table magazine
                that won't load.
              </li>
            </ul>
          </li>
          <li>
            Judge whether the compositions sit in the editorial-instrument
            tension between those poles. Sign off, request iteration, or
            redirect.
          </li>
        </ol>
      </section>

      <section className="space-y-4">
        <h2 className="type-heading">Evidence</h2>
        <ul className="space-y-3">
          {EVIDENCE.map((entry) => (
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
                <p className="type-heading text-foreground mb-2">
                  {entry.title}
                </p>
                <p className="type-body text-muted-foreground">{entry.what}</p>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="border-border space-y-4 rounded-lg border border-dashed p-6">
        <h2 className="type-heading">The agent's articulation</h2>
        <p className="type-body text-muted-foreground max-w-prose">
          This is the framing — not a self-certification. The agent argues that
          the compositions hold the tension because:
        </p>
        <ul className="type-body text-muted-foreground max-w-prose space-y-3 list-disc pl-5 marker:text-foreground/40">
          <li>
            <strong className="text-foreground">
              Warmth lives in the typography and the palette:
            </strong>{" "}
            Fraunces on persona names, warm OKLCH paper and ink, the curated
            identity-colour palette in two zones of accent-grade hues. Vermilion{" "}
            <code className="type-code">--primary</code> stays the heaviest
            accent in every composition — the brand stays loud, the personas
            stay distinct.
          </li>
          <li>
            <strong className="text-foreground">
              Precision lives in the restraint:
            </strong>{" "}
            three accents per persona (avatar + name underline + 2px border),
            never a surface tint. The neutral message body keeps the persona's
            words the figure, not her colour. The tier badge shows the system's
            work without becoming a dashboard.
          </li>
          <li>
            <strong className="text-foreground">
              The tension is visible in the type pairing:
            </strong>{" "}
            Fraunces (editorial) + Geist (instrument) on every page. The
            persona-name underline is in Fraunces-adjacent territory; the body
            face is the precise grotesque that lets it read.
          </li>
          <li>
            <strong className="text-foreground">
              Multi-persona views scale:
            </strong>{" "}
            Astrid, Kai, and Maren read as three different individuals in the
            persona list (T08), and would continue to scale to 10+ personas
            without fruit-salad because no content surface is ever
            colour-tinted.
          </li>
        </ul>
        <p className="type-body text-muted-foreground max-w-prose">
          Where the agent has open questions:
        </p>
        <ul className="type-body text-muted-foreground max-w-prose space-y-2 list-disc pl-5 marker:text-foreground/40">
          <li>
            Is the 1px identity-coloured name underline (B's contribution in the
            D-F1-5 composite) too subtle on the light theme? Tunable to 2px in
            one CSS edit.
          </li>
          <li>
            Does the agentic-run composition (T10) tip too far toward
            instrument-pole? The cool border + the timeline dots have a
            dashboard feel; consider warming the surrounding margins.
          </li>
          <li>
            The empty state (T11) leans editorial — the "P" mark in Fraunces is
            a deliberate flourish. Does it read warm or pretentious?
          </li>
        </ul>
      </section>

      <section className="border-primary/40 bg-primary/5 space-y-3 rounded-lg border p-6">
        <h2 className="type-heading">Hand-off</h2>
        <p className="type-body text-foreground">
          Criterion #7 is <strong>prepared for human design review</strong>. The
          agent has stopped here — does not self-certify the north-star tension
          is held. Sign off, request iteration, or redirect; Phase 6 stays
          paused until #7 closes.
        </p>
        <p className="type-caption text-muted-foreground">
          The supporting paper artefact:{" "}
          <Link className="underline" href="/reference">
            docs/specs/phase2/spec_F1/evidence_package.md
          </Link>
          .
        </p>
      </section>
    </div>
  );
}
