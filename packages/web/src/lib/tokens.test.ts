/**
 * Spec F1 T02 + T03 — token foundation smoke tests.
 *
 * Asserts that the design tokens this spec depends on exist in globals.css
 * with the expected names + a value in both light (:root) and dark (.dark)
 * selectors where mode-dependent. CSS parsing is text-based (regex) — there
 * is no jsdom layout pass, which keeps these tests fast and deterministic.
 *
 * What this covers:
 * - The tier temperature scale (T03 — confirms scaffold names + dark-mode swap).
 * - The new motion / elevation / type-scale / identity tokens (T02 — confirms
 *   they landed additively in @theme inline).
 *
 * What this does NOT cover:
 * - Visual rendering (the reference compositions T07–T12 cover that).
 * - Contrast ratios (T14 covers that — programmatic AA + CVD).
 * - Tailwind utility resolution (the build step covers that — Tailwind v4 panic-
 *   fails the build if an @theme entry doesn't resolve).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const GLOBALS_CSS = readFileSync(
  resolve(__dirname, "../app/globals.css"),
  "utf-8",
);

function block(selector: string): string {
  // Extracts the *first* CSS block for the given selector. Works for `:root`,
  // `.dark`, and `@theme inline` shapes. Naive (assumes balanced braces with no
  // nested `{ ... }` inside string values — fine for our globals.css).
  const start = GLOBALS_CSS.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`selector "${selector}" not found`);
  const open = GLOBALS_CSS.indexOf("{", start);
  let depth = 1;
  let i = open + 1;
  while (depth > 0 && i < GLOBALS_CSS.length) {
    if (GLOBALS_CSS[i] === "{") depth++;
    else if (GLOBALS_CSS[i] === "}") depth--;
    i++;
  }
  return GLOBALS_CSS.slice(open + 1, i - 1);
}

const theme = block("@theme inline");
const rootLight = block(":root");
const rootDark = block(".dark");

describe("Spec F1 T03 — tier temperature scale", () => {
  it("declares all three tier tokens in @theme inline as Tailwind color tokens", () => {
    expect(theme).toMatch(/--color-tier-frontier:\s*var\(--tier-frontier\)/);
    expect(theme).toMatch(/--color-tier-mid:\s*var\(--tier-mid\)/);
    expect(theme).toMatch(/--color-tier-small:\s*var\(--tier-small\)/);
  });

  it("provides all three tier tokens in light mode (:root)", () => {
    expect(rootLight).toMatch(/--tier-frontier:\s*oklch\([^)]+\)/);
    expect(rootLight).toMatch(/--tier-mid:\s*oklch\([^)]+\)/);
    expect(rootLight).toMatch(/--tier-small:\s*oklch\([^)]+\)/);
  });

  it("provides all three tier tokens in dark mode (.dark)", () => {
    expect(rootDark).toMatch(/--tier-frontier:\s*oklch\([^)]+\)/);
    expect(rootDark).toMatch(/--tier-mid:\s*oklch\([^)]+\)/);
    expect(rootDark).toMatch(/--tier-small:\s*oklch\([^)]+\)/);
  });

  it("preserves the cool→warm→hot hue progression in light mode", () => {
    // hue 232 (slate) → 73 (amber) → 30 (vermilion). Source-as-truth: the
    // OKLCH triples are inline in globals.css; if a future contributor "fixes"
    // the lightness non-monotonicity by re-hueing, this test catches it.
    const small = rootLight.match(/--tier-small:\s*oklch\(([^)]+)\)/)?.[1];
    const mid = rootLight.match(/--tier-mid:\s*oklch\(([^)]+)\)/)?.[1];
    const frontier = rootLight.match(
      /--tier-frontier:\s*oklch\(([^)]+)\)/,
    )?.[1];
    expect(small).toMatch(/\b232\b/);
    // hue 70 in light mode (darkened from the original 73 during T14 to clear
    // 3:1 WCAG-AA contrast on the paper background — see globals.css comment
    // and DESIGN.md).
    expect(mid).toMatch(/\b70\b/);
    expect(frontier).toMatch(/\b30\b/);
  });

  it("preserves the load-bearing chroma escalation (small < mid < frontier)", () => {
    // Chroma is the signal that reads as "firepower." Asserted in both modes.
    for (const css of [rootLight, rootDark]) {
      const small = Number.parseFloat(
        css.match(/--tier-small:\s*oklch\([^\s]+\s+([^\s]+)\s+[^)]+\)/)?.[1] ??
          "0",
      );
      const mid = Number.parseFloat(
        css.match(/--tier-mid:\s*oklch\([^\s]+\s+([^\s]+)\s+[^)]+\)/)?.[1] ??
          "0",
      );
      const frontier = Number.parseFloat(
        css.match(
          /--tier-frontier:\s*oklch\([^\s]+\s+([^\s]+)\s+[^)]+\)/,
        )?.[1] ?? "0",
      );
      expect(small).toBeLessThan(mid);
      expect(mid).toBeLessThan(frontier);
    }
  });

  it("keeps --tier-frontier in lock-step with --primary in light mode", () => {
    // The intentional semantic overload (D-F1-6 / T03 comment): frontier == primary.
    // If a future contributor splits them, this test catches it — and the split
    // becomes a deliberate change, not an accident.
    const frontier = rootLight.match(
      /--tier-frontier:\s*oklch\(([^)]+)\)/,
    )?.[1];
    const primary = rootLight.match(/--primary:\s*oklch\(([^)]+)\)/)?.[1];
    expect(frontier).toBe(primary);
  });

  it("keeps --tier-frontier in lock-step with --primary in dark mode", () => {
    const frontier = rootDark.match(/--tier-frontier:\s*oklch\(([^)]+)\)/)?.[1];
    const primary = rootDark.match(/--primary:\s*oklch\(([^)]+)\)/)?.[1];
    expect(frontier).toBe(primary);
  });
});

describe("Spec F1 T02 — motion tokens", () => {
  it("declares the three motion durations and two easings", () => {
    expect(theme).toMatch(/--motion-duration-fast:\s*120ms/);
    expect(theme).toMatch(/--motion-duration-normal:\s*200ms/);
    expect(theme).toMatch(/--motion-duration-slow:\s*320ms/);
    expect(theme).toMatch(/--motion-ease-standard:\s*cubic-bezier\(/);
    expect(theme).toMatch(/--motion-ease-emphasized:\s*cubic-bezier\(/);
  });
});

describe("Spec F1 T02 — elevation tokens", () => {
  it("declares all four elevation tiers", () => {
    expect(theme).toMatch(/--elevation-0:\s*none/);
    expect(theme).toMatch(/--elevation-1:\s*0\s+1px\s+2px/);
    expect(theme).toMatch(/--elevation-2:/);
    expect(theme).toMatch(/--elevation-3:/);
  });
});

describe("Spec F1 T02 — type-scale tokens", () => {
  it("declares size + line-height pairs for all six type roles", () => {
    for (const role of [
      "display",
      "heading",
      "body",
      "ui",
      "caption",
      "code",
    ]) {
      expect(theme).toMatch(new RegExp(`--text-${role}-size:\\s*[\\d.]+rem`));
      expect(theme).toMatch(
        new RegExp(`--text-${role}-line-height:\\s*[\\d.]+`),
      );
    }
  });

  it("sets --text-caption-size to the 0.65rem that resolves the 5x text-[0.65rem] magic", () => {
    expect(theme).toMatch(/--text-caption-size:\s*0\.65rem/);
  });
});

describe("Spec F1 T02 — persona identity scaffolding", () => {
  it("declares --identity-h / --identity-l / --identity-c with brand-vermilion fallback", () => {
    expect(theme).toMatch(/--identity-h:\s*30\b/);
    expect(theme).toMatch(/--identity-l:\s*0\.585/);
    expect(theme).toMatch(/--identity-c:\s*0\.196/);
  });
});

describe("Spec F1 T01 — additive-only contract (D-F1-7)", () => {
  it("preserves the load-bearing scaffold token names that shadcn primitives + Clerk consume", () => {
    // If any of these are renamed, 14 shadcn primitives + Clerk's surface
    // inheritance + 5+ existing pages break. D-F1-7 lock.
    for (const name of [
      "--primary",
      "--primary-foreground",
      "--secondary",
      "--accent",
      "--accent-foreground",
      "--muted",
      "--muted-foreground",
      "--card",
      "--card-foreground",
      "--popover",
      "--popover-foreground",
      "--border",
      "--input",
      "--ring",
      "--background",
      "--foreground",
      "--destructive",
    ]) {
      expect(rootLight).toMatch(new RegExp(`${name}:\\s*oklch\\(`));
      expect(rootDark).toMatch(new RegExp(`${name}:\\s*oklch\\(`));
    }
  });
});
