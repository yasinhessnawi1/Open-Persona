/**
 * Spec F1 T09 — Reference composition: persona-authoring moment.
 *
 * A representative step of the one-sentence-to-persona flow — the moment
 * where the draft is shown in the structured form with a clarifying-question
 * card waiting beside it. Fixture-fed; reuses the visual shapes from spec-10's
 * authoring UX without reimplementing them.
 *
 * The editorial half of the north star: authoring feels like *drafting an
 * article in a considered tool*, not like *filling out a configuration form*.
 * Fraunces in the right place (the working title); body face for the form
 * labels; the clarifying-question card is warm, inviting, not a system alert.
 */
import { PersonaAvatar } from "@/components/persona/persona-avatar";

const draftPersona = {
  id: "draft_norwegian_legal_assistant",
  name: "Astrid",
};

export default function AuthoringReferencePage() {
  return (
    <div className="space-y-10">
      <header className="space-y-2">
        <p className="type-caption text-muted-foreground">T09 · §11.6</p>
        <h1 className="type-display">Authoring — Astrid (draft)</h1>
        <p className="type-body text-muted-foreground max-w-prose">
          The marquee UX moment: one sentence in, structured persona drafted,
          clarifying questions waiting on the side. The editorial drafting feel.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1fr_280px]">
        <article className="border-border bg-card space-y-6 rounded-lg border p-6">
          <header className="flex items-center gap-4">
            <PersonaAvatar persona={draftPersona} size="lg" />
            <div className="space-y-1">
              <p className="type-caption text-muted-foreground">
                Draft persona
              </p>
              <p className="type-display leading-tight">Astrid</p>
            </div>
          </header>

          <section className="space-y-3">
            <p className="type-caption text-muted-foreground">Identity</p>
            <dl className="space-y-3">
              <div>
                <dt className="type-ui text-muted-foreground mb-1">Role</dt>
                <dd className="type-body text-foreground border-border bg-background rounded border px-3 py-2">
                  Norwegian tenancy law assistant
                </dd>
              </div>
              <div>
                <dt className="type-ui text-muted-foreground mb-1">
                  Background
                </dt>
                <dd className="type-body text-foreground border-border bg-background rounded border px-3 py-2">
                  Trained on Norwegian rental law (husleieloven). Speaks
                  Norwegian and English. Conservative — always recommends a
                  qualified lawyer for disputes.
                </dd>
              </div>
            </dl>
          </section>

          <section className="space-y-3">
            <p className="type-caption text-muted-foreground">
              Self-facts <span className="text-muted-foreground/60">· 2</span>
            </p>
            <ul className="space-y-2">
              <li className="type-body text-foreground border-border bg-background flex items-start gap-3 rounded border px-3 py-2">
                <span className="type-caption text-tier-frontier mt-1">
                  1.0
                </span>
                <span>
                  Specialised in Norwegian residential tenancy (husleieloven
                  kap. 1–13).
                </span>
              </li>
              <li className="type-body text-foreground border-border bg-background flex items-start gap-3 rounded border px-3 py-2">
                <span className="type-caption text-tier-frontier mt-1">
                  1.0
                </span>
                <span>
                  Cannot represent users in court; only provides information and
                  document drafting.
                </span>
              </li>
            </ul>
          </section>

          <section className="space-y-3">
            <p className="type-caption text-muted-foreground">
              Constraints <span className="text-muted-foreground/60">· 3</span>
            </p>
            <ul className="space-y-2">
              <li className="type-body text-foreground border-border bg-background rounded border px-3 py-2">
                Never give binding legal advice; always recommend a qualified
                lawyer for disputes.
              </li>
              <li className="type-body text-foreground border-border bg-background rounded border px-3 py-2">
                Do not draft legal filings without explicit user confirmation.
              </li>
              <li className="type-body text-foreground border-border bg-background rounded border px-3 py-2">
                Cite husleieloven section numbers when making claims about
                Norwegian law.
              </li>
            </ul>
          </section>
        </article>

        <aside className="space-y-4">
          <div className="border-primary/30 bg-primary/5 space-y-3 rounded-lg border p-5">
            <p className="type-caption text-primary">Tell me more</p>
            <p className="type-body text-foreground">
              Should Astrid handle commercial leases too, or stay focused on
              residential?
            </p>
            <p className="type-caption text-muted-foreground">
              Round 1 of 3 · optional
            </p>
            <div className="flex gap-2 pt-1">
              <button
                type="button"
                className="bg-primary text-primary-foreground type-ui rounded px-3 py-1.5 font-medium"
              >
                Answer
              </button>
              <button
                type="button"
                className="text-muted-foreground type-ui hover:text-foreground rounded px-3 py-1.5"
              >
                Skip
              </button>
            </div>
          </div>

          <div className="border-border space-y-2 rounded-lg border p-5">
            <p className="type-caption text-muted-foreground">Next</p>
            <button
              type="button"
              className="bg-primary text-primary-foreground type-ui hover:bg-primary/90 w-full rounded px-4 py-2 font-medium transition-colors"
            >
              Save persona
            </button>
            <button
              type="button"
              className="border-border text-muted-foreground type-ui hover:text-foreground w-full rounded border px-4 py-2 transition-colors"
            >
              Edit YAML
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}
