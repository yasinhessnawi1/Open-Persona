/**
 * Spec F1 T15 — reduced-motion smoke tests (criterion #11.10).
 *
 * Asserts the `@media (prefers-reduced-motion: reduce)` block exists in
 * globals.css and overrides the motion-duration tokens + animation durations.
 * Text-parses globals.css (consistent with tokens.test.ts) — no jsdom layout
 * pass, no fragile media-query emulation.
 *
 * What this covers:
 * - The reduced-motion CSS rule exists.
 * - Motion-duration tokens collapse to ≤1ms under reduced motion.
 * - Universal animation-duration / transition-duration overrides exist (so
 *   the bare-Tailwind animate-pulse and tw-animate-css states honour the
 *   user preference).
 *
 * What this does NOT cover:
 * - A real browser actually applying the rule (Playwright covers that in
 *   the future polish suite; for F1 the static CSS check is the audit point).
 * - The functional streaming-text path (intentionally NOT zeroed — verified
 *   by reading the T07 ChatComposition source).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const GLOBALS_CSS = readFileSync(
  resolve(__dirname, "../app/globals.css"),
  "utf-8",
);

describe("Spec F1 T15 — reduced-motion path (criterion #11.10)", () => {
  it("declares an @media (prefers-reduced-motion: reduce) block", () => {
    expect(GLOBALS_CSS).toMatch(
      /@media\s*\(\s*prefers-reduced-motion:\s*reduce\s*\)/,
    );
  });

  // These tokens / overrides ONLY appear in the reduced-motion block (the
  // base @theme tokens are 120/200/320ms, never 0.01ms). String-match on the
  // whole file is sufficient and avoids brittle brace-balancing parsing.

  it("overrides the three motion-duration tokens to 0.01ms under reduced motion", () => {
    expect(GLOBALS_CSS).toMatch(/--motion-duration-fast:\s*0\.01ms/);
    expect(GLOBALS_CSS).toMatch(/--motion-duration-normal:\s*0\.01ms/);
    expect(GLOBALS_CSS).toMatch(/--motion-duration-slow:\s*0\.01ms/);
  });

  it("zeros animation-duration and transition-duration globally with !important", () => {
    expect(GLOBALS_CSS).toMatch(/animation-duration:\s*0\.01ms\s*!important/);
    expect(GLOBALS_CSS).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
  });

  it("uses 0.01ms (not 0ms) so transitionend / animationend events still fire", () => {
    // Functional code that listens for transitionend (e.g., shadcn primitives'
    // open/close state machines) breaks if duration is hard-zeroed. 0.01ms is
    // the standard "instant but still firing" idiom.
    expect(GLOBALS_CSS).not.toMatch(
      /(animation|transition)-duration:\s*0ms\s*!important/,
    );
    expect(GLOBALS_CSS).not.toMatch(
      /(animation|transition)-duration:\s*0\s*!important/,
    );
  });
});
