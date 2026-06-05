/**
 * Spec F1 T11 — Reference composition: empty state.
 *
 * The §8 UI voice surface — empty personas list. Microcopy in the chosen
 * editorial voice: warm, clear, invites rather than apologises. The point of
 * this composition is the *tone*, not the layout.
 *
 * What to look at:
 *   - The headline is invitational ("Who do you want to talk to?"), not
 *     defeated ("No personas yet").
 *   - The body copy explains the next step in a single warm sentence, not a
 *     list of instructions.
 *   - The CTA names the action concretely ("Describe one in a sentence"),
 *     not generically ("Get started" / "Create new").
 *   - The illustration slot is a small editorial mark (a Fraunces glyph in
 *     identity-coloured strokes), not a generic empty-state cartoon.
 */
import Link from "next/link";

export default function EmptyReferencePage() {
  return (
    <div className="space-y-10">
      <header className="space-y-2">
        <p className="type-caption text-muted-foreground">
          T11 · §11.6 · UI voice
        </p>
        <h1 className="type-display">Empty state</h1>
        <p className="type-body text-muted-foreground max-w-prose">
          A user lands on /personas with no personas yet. The empty state
          invites rather than apologises.
        </p>
      </header>

      <section className="border-border bg-card rounded-lg border p-10">
        <div className="mx-auto flex max-w-md flex-col items-center text-center">
          {/* Editorial mark — a Fraunces uppercase "P" used as an illustrative
              glyph rather than an empty-state cartoon. The font-heading family
              + the warm muted ink keep it consistent with the wider language. */}
          <div
            aria-hidden
            className="bg-muted/40 text-foreground/40 mb-6 grid size-24 place-items-center rounded-full font-heading text-6xl"
          >
            P
          </div>

          <h2 className="type-display text-foreground mb-3 leading-tight">
            Who do you want to talk to?
          </h2>

          <p className="type-body text-muted-foreground mb-8">
            A persona is someone with a role, a point of view, and the
            constraints that keep them honest. Describe one in a sentence —
            Astrid the Norwegian tenancy law assistant, or Maren who edits your
            writing — and Persona drafts the rest.
          </p>

          <Link
            href="/reference/author"
            className="bg-primary text-primary-foreground type-ui hover:bg-primary/90 inline-flex items-center gap-2 rounded-lg px-5 py-2.5 font-medium transition-colors"
          >
            Describe one in a sentence
          </Link>

          <p className="type-caption text-muted-foreground mt-6">
            or{" "}
            <Link
              href="/reference/author"
              className="underline-offset-2 hover:text-foreground underline"
            >
              import a YAML
            </Link>
          </p>
        </div>
      </section>

      <aside className="border-border bg-muted/30 space-y-3 rounded-lg border border-dashed p-5">
        <h2 className="type-heading">Read the voice</h2>
        <p className="type-body text-muted-foreground max-w-prose">
          Compare to a sterile alternative ("You have no personas. Click below
          to create one.") and a fussy alternative ("Welcome! We're so excited
          to help you build your first persona today!"). The composition above
          sits between — confident, warm, never cute, never apologetic.
        </p>
      </aside>
    </div>
  );
}
