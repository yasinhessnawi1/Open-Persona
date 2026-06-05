/**
 * Spec F1 T08 — Reference composition: persona list (Astrid + Kai + Maren).
 *
 * THE §4 individuality-within-coherence proof. Three personas, same template,
 * each consuming the derived identity colour. The acceptance: varied AND
 * recognisable, never fruit-salad. If the three read as recognisably different
 * people in recognisably the same product, the design language works.
 *
 * Every card uses the same shape — only avatar, identity-colour underline,
 * name, role differ. The variation comes from the LANGUAGE (avatar fill +
 * name underline derived from the persona), not from per-card design.
 *
 * What to look at:
 *   - Three distinct individuals at a glance — Astrid, Kai, Maren read
 *     differently from across the room, before any text is parsed.
 *   - The cards are visually identical in structure — no one persona is
 *     "louder" than another; the design language treats them symmetrically.
 *   - The vermilion --primary stays the strongest accent on the page (the
 *     "New persona" CTA button); no persona out-shouts the brand.
 *   - Identity colours harmonise with the warm paper base; none clashes.
 */
import Link from "next/link";
import { PersonaAvatar } from "@/components/persona/persona-avatar";
import { personaIdentityStyle } from "@/lib/persona-identity";
import { REFERENCE_PERSONAS, type ReferencePersona } from "../_fixtures";

const IDENTITY_OKLCH =
  "oklch(var(--identity-l) var(--identity-c) var(--identity-h))";

function PersonaCard({ persona }: { persona: ReferencePersona }) {
  return (
    <article
      style={personaIdentityStyle(persona)}
      className="border-border bg-card hover:border-primary/30 flex flex-col gap-4 rounded-lg border p-5 transition-colors"
    >
      <header className="flex items-start gap-4">
        <PersonaAvatar persona={persona} size="lg" />
        <div className="min-w-0 flex-1 space-y-1">
          <p
            // 1px identity-coloured underline beneath the name — the D-F1-5
            // composite consistent across chat (T07) and list (T08).
            style={{
              borderBottomColor: IDENTITY_OKLCH,
              borderBottomWidth: "1px",
              borderBottomStyle: "solid",
            }}
            className="type-heading inline-block"
          >
            {persona.name}
          </p>
          <p className="type-ui text-muted-foreground">{persona.role}</p>
        </div>
      </header>
      <p className="type-body text-muted-foreground/90 italic">
        {persona.character}
      </p>
      <footer className="border-border flex items-center justify-between border-t pt-3">
        <span className="type-caption text-muted-foreground">
          12 conversations
        </span>
        <span className="type-caption text-primary">Open →</span>
      </footer>
    </article>
  );
}

export default function PersonasReferencePage() {
  return (
    <div className="space-y-10">
      <header className="flex items-end justify-between gap-6">
        <div className="space-y-2">
          <p className="type-caption text-muted-foreground">T08 · §4 proof</p>
          <h1 className="type-display">Your personas</h1>
          <p className="type-body text-muted-foreground max-w-prose">
            The §4 multi-persona test. Three distinct individuals in one
            coherent product — varied and recognisable, never fruit-salad.
          </p>
        </div>
        <Link
          href="/reference"
          className="bg-primary text-primary-foreground type-ui font-medium hover:bg-primary/90 inline-flex items-center gap-2 rounded-lg px-4 py-2 transition-colors"
        >
          New persona
        </Link>
      </header>

      <section className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {REFERENCE_PERSONAS.map((persona) => (
          <PersonaCard key={persona.id} persona={persona} />
        ))}
      </section>

      <aside className="border-border bg-muted/30 space-y-3 rounded-lg border border-dashed p-5">
        <h2 className="type-heading">Read the proof</h2>
        <p className="type-body text-muted-foreground max-w-prose">
          Look at the three cards side-by-side. Astrid, Kai, and Maren should
          feel like three different people <em>before</em> you read the role
          line. The identity colour does most of the recognition work; the
          avatar mark + name underline reinforce it; the card structure is
          identical. If one persona reads as "louder" or "lesser" than the
          others, the design language has failed. If they look like the same
          card with different name tags, the identity work has been too timid.
        </p>
      </aside>
    </div>
  );
}
