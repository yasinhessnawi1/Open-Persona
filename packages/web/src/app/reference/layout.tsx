/**
 * Spec F1 — Reference compositions layout.
 *
 * The `/reference/*` route group hosts fixture-fed static screens that prove
 * the F1 design language composes. NOT live, NOT auth-protected (the Clerk
 * proxy.ts whitelists `/reference(.*)` by virtue of not matching the
 * protected route list), NOT part of the production navigation. The agent
 * stops at T16's `/reference/review` index; the human signs off criterion #7.
 *
 * Routes:
 *   /reference/swatches       T05 — 12-persona identity-colour swatch sheet
 *   /reference/chat           T07 — chat with Astrid (light)
 *   /reference/personas       T08 — persona list (Astrid + Kai + Maren)
 *   /reference/author         T09 — authoring moment
 *   /reference/run            T10 — agentic run unfolding
 *   /reference/empty          T11 — empty state
 *   /reference/chat-dark      T12 — chat in dark mode
 *   /reference/review         T16 — criterion-#7 evidence index
 *
 * This layout is intentionally MINIMAL — no app shell, no sidebar, no chrome.
 * Each reference page provides its own header so the composition is judged
 * on its own merit, not on the surrounding app frame.
 */
import Link from "next/link";

export default function ReferenceLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="bg-background text-foreground min-h-dvh">
      <header className="border-border border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
          <Link
            href="/reference"
            className="type-caption text-muted-foreground hover:text-foreground transition-colors"
          >
            Reference Compositions · Spec F1
          </Link>
          <span className="type-caption text-muted-foreground">
            Fixture-fed · not live
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
    </div>
  );
}
