"use client";

/**
 * Spec F2 T23 — sonner integration spike (D-F2-10 lock-condition verification).
 *
 * Per the user's Phase-5 directive: verify sonner's integration against
 * F1's tokens before locking D-F2-10. Three checks the spike covers:
 *
 *   1. **Token consumption.** Each variant (default/success/info/warning/error)
 *      resolves through F1's @theme inline block — colours, surface, border,
 *      text-foreground all from --bg/--fg/--primary/--destructive/--tier-mid
 *      tokens. Buttons inside toasts use --primary. No literal hex anywhere.
 *
 *   2. **Motion-token consumption.** Sonner ships with internal transitions
 *      (~400ms default); we override via a scoped CSS block to consume
 *      --motion-duration-normal (200ms) so motion duration declares F1
 *      intent. The F1 T15 universal `transition-duration: 0.01ms !important`
 *      catches sonner under prefers-reduced-motion regardless — structural
 *      defence. The override is for explicit semantic consumption.
 *
 *   3. **Reduced-motion behaviour.** Toggle the OS prefers-reduced-motion
 *      flag (or use DevTools `Emulate CSS prefers-reduced-motion: reduce`)
 *      and verify each variant still functions (toast appears + dismisses)
 *      without animation. F1 T15's path silences slide+fade automatically.
 *
 * Dev-only: NODE_ENV guard throws in production. Harness stays in-tree per
 * Phase 3 refinement 1 — re-runnable for the T34 criterion-#11 review.
 *
 * Visit /scratch/toasts in dev. Click each variant button to fire a toast;
 * observe colour resolution + animation timing. Toggle OS reduced-motion
 * and re-fire to verify the silencing path.
 */

import { Toaster, toast } from "sonner";

if (process.env.NODE_ENV === "production") {
  throw new Error(
    "/scratch/* routes are development-only. This page must not render in production.",
  );
}

export default function ScratchToastsPage() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 p-8">
      <header className="space-y-2">
        <h1 className="type-display">T23 sonner spike</h1>
        <p className="type-ui text-muted-foreground">
          D-F2-10 lock-condition verification. Each button fires a sonner toast
          variant; visually verify (a) colours resolve through F1 tokens (no
          literal hex anywhere); (b) motion-duration consumes
          --motion-duration-normal via the scoped override below; (c) under
          prefers-reduced-motion, F1 T15's universal !important silences
          sonner's slide+fade. The Toaster is wired with richColors +
          theme-aware to match the editorial-instrument feel.
        </p>
      </header>

      <section className="space-y-3 rounded-lg border bg-card p-4">
        <h2 className="type-heading">Variants</h2>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => toast("Astrid has updated her self-facts.")}
            className="rounded border border-border bg-background px-3 py-1.5 text-sm hover:bg-muted"
          >
            Default toast
          </button>
          <button
            type="button"
            onClick={() => toast.success("Persona saved successfully.")}
            className="rounded border border-tier-small/40 bg-background px-3 py-1.5 text-sm text-foreground hover:bg-muted"
          >
            Success
          </button>
          <button
            type="button"
            onClick={() =>
              toast.info("Hosted-tier routing engaged for this turn.")
            }
            className="rounded border border-tier-mid/40 bg-background px-3 py-1.5 text-sm text-foreground hover:bg-muted"
          >
            Info
          </button>
          <button
            type="button"
            onClick={() =>
              toast.warning(
                "Approaching credit threshold. Consider topping up.",
              )
            }
            className="rounded border border-tier-mid/50 bg-background px-3 py-1.5 text-sm text-foreground hover:bg-muted"
          >
            Warning
          </button>
          <button
            type="button"
            onClick={() => toast.error("Rate-limited. Retrying in 30 seconds.")}
            className="rounded border border-destructive/40 bg-background px-3 py-1.5 text-sm text-foreground hover:bg-muted"
          >
            Error
          </button>
          <button
            type="button"
            onClick={() =>
              toast("Run dispatched to frontier tier.", {
                action: {
                  label: "View",
                  onClick: () => toast.success("Navigating to /runs..."),
                },
              })
            }
            className="rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground hover:bg-primary/80"
          >
            With action
          </button>
        </div>
      </section>

      <section className="space-y-2 rounded-lg border bg-card p-4">
        <h2 className="type-heading">Verification checklist</h2>
        <ul className="type-body list-disc space-y-1.5 pl-5 text-muted-foreground">
          <li>
            Each variant's surface resolves through --background + --foreground
            + the relevant accent token (success → border-tier-small/40, etc.);
            no inline hex anywhere.
          </li>
          <li>
            Animation duration matches --motion-duration-normal (200ms) instead
            of sonner's default ~400ms — visible as a snappier entrance/exit.
          </li>
          <li>
            Under OS prefers-reduced-motion (or DevTools emulation), each toast
            appears + dismisses without slide/fade animation; functional
            appearance preserved.
          </li>
          <li>
            "With action" toast's action button is keyboard-focusable; Esc
            dismisses the toast.
          </li>
        </ul>
      </section>

      {/*
       * <Toaster> wiring — the F1 token-binding live here so the spike is
       * self-contained. Production usage (T23 <ToastProvider>) wraps the
       * AppShell with the same configuration.
       */}
      <Toaster
        richColors
        position="top-right"
        // F1 motion-token override via the toastOptions style prop. Sonner's
        // internal transitions read CSS `transition-duration` from this hook;
        // we point it at --motion-duration-normal so toast motion declares
        // F1 intent. F1 T15's universal !important silences under
        // prefers-reduced-motion regardless.
        toastOptions={{
          style: {
            transitionDuration: "var(--motion-duration-normal)",
          },
        }}
      />
    </main>
  );
}
