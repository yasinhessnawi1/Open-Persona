"use client";

/**
 * Spec F2 T23 — Toast pattern (D-F2-10 locked: sonner).
 *
 * Production wrapper around sonner. Two exports:
 *
 *   - <ToastProvider /> — render once near the root of the auth'd app
 *     (T19 <AppShell> composes it). Configures the <Toaster /> with
 *     F1 token consumption: position, theme (light/dark via next-themes),
 *     richColors variant binding, --motion-duration-normal override.
 *
 *   - useToast() — returns the imperative `toast(...)` family. Direct
 *     re-export of sonner's API; the hook shape lets future call-sites
 *     swap implementations behind the same surface without breaking
 *     consumers.
 *
 * Transitive audit (D-F2-10 lock-condition verification, 2026-06-05):
 *   - sonner@2.0.7
 *   - Runtime dependencies: ZERO (self-contained library).
 *   - Peer deps: react@^18|^19 (we ship 19.2.4 — satisfied).
 *   - License: MIT (Apache-2.0 / project-clean).
 *   - Verified at `/scratch/toasts` — each variant resolves through
 *     F1 tokens, motion-duration-normal override engaged, F1 T15
 *     reduced-motion path silences via universal !important.
 *
 * F1 motion-token consumption (the lock-condition empirical check):
 *   The `toastOptions.style.transitionDuration` sets the toast surface's
 *   CSS `transition-duration`. Sonner's internal slide/fade reads from
 *   it; we point it at --motion-duration-normal (200ms) instead of
 *   sonner's default ~400ms. F1 T15's universal `transition-duration:
 *   0.01ms !important` under prefers-reduced-motion catches sonner
 *   regardless — structural defence, not per-component discipline.
 */

import { useTheme } from "next-themes";
import { Toaster, toast } from "sonner";

/**
 * The toast surface for the auth'd app. Render once in <AppShell> (T19).
 * Consumes F1 tokens for theming + motion via sonner's documented
 * theming hooks (the `theme` prop + the `toastOptions.style` override).
 */
export function ToastProvider() {
  const { theme } = useTheme();
  return (
    <Toaster
      richColors
      position="top-right"
      // sonner's theme prop drives its variant colour resolution. We pass
      // the next-themes resolved theme so light/dark switch correctly with
      // the rest of the F1 token swap (D-09-10).
      theme={(theme as "light" | "dark" | "system" | undefined) ?? "system"}
      toastOptions={{
        style: {
          transitionDuration: "var(--motion-duration-normal)",
        },
      }}
    />
  );
}

/**
 * Imperative toast API. Returns the sonner `toast` namespace which carries
 * `toast()`, `toast.success()`, `toast.info()`, `toast.warning()`,
 * `toast.error()`, `toast.dismiss()`. The hook shape (vs direct import)
 * decouples consumers from the sonner module so future swaps stay
 * one-edit-per-implementation.
 */
export function useToast() {
  return toast;
}

// Re-export the imperative `toast` for cases where a hook isn't applicable
// (server actions, utility modules). Same surface as useToast()'s return.
export { toast };
