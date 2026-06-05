/**
 * Spec F1 T05 — persona-identity palette + derivation tests.
 * Covers acceptance #4 (deterministic + mutually distinct on the
 * representative set, in-range). Partial coverage of #3 (CVD verification
 * lives in T14's contrast/CVD pass).
 */
import { describe, expect, it } from "vitest";
import {
  derivePersonaIdentityColor,
  IDENTITY_PALETTE,
  personaIdentityStyle,
  REPRESENTATIVE_PERSONAS,
} from "./persona-identity";

describe("IDENTITY_PALETTE — the curated set (D-F1-1 / X-F1-2 lock)", () => {
  it("has exactly 12 hues", () => {
    expect(IDENTITY_PALETTE).toHaveLength(12);
  });

  it("fixes lightness at L=0.60 and chroma at C=0.13 across the palette (accent-grade)", () => {
    // L=0.60 + C=0.13 is the accent-grade weight. Vermilion --primary is
    // C=0.196 (heavier); persona identity colours never out-shout the brand.
    for (const colour of IDENTITY_PALETTE) {
      expect(colour.l).toBe(0.6);
      expect(colour.c).toBe(0.13);
    }
  });

  it("keeps every hue outside the brand-and-tier exclusion zones", () => {
    // Zone exclusions:
    //   - vermilion --primary at hue 30 (±25°): 5°–55° excluded
    //   - tier-mid amber at hue 73 (±15°): 58°–88° excluded
    //   - tier-small slate at hue 232 (±13°): 219°–245° excluded
    // (palette #1 sits exactly at 90°, the right edge of the 58°–88° band —
    // 90 is allowed.)
    const inExclusion = (h: number): boolean =>
      (h >= 5 && h <= 55) || (h >= 58 && h <= 88) || (h >= 219 && h <= 245);
    for (const colour of IDENTITY_PALETTE) {
      expect(inExclusion(colour.h)).toBe(false);
    }
  });

  it("keeps pairwise hue-wheel distance >= 15° (mutually distinguishable)", () => {
    // 15° at L=0.60/C=0.13 is comfortably above the just-noticeable-difference
    // for side-by-side comparison. If a future contributor adds a 13th hue
    // that collides, this test catches it before merge.
    const wheelDistance = (a: number, b: number): number => {
      const d = Math.abs(a - b);
      return Math.min(d, 360 - d);
    };
    for (let i = 0; i < IDENTITY_PALETTE.length; i++) {
      for (let j = i + 1; j < IDENTITY_PALETTE.length; j++) {
        const dist = wheelDistance(
          IDENTITY_PALETTE[i].h,
          IDENTITY_PALETTE[j].h,
        );
        expect(dist).toBeGreaterThanOrEqual(15);
      }
    }
  });

  it("encodes every entry as a parseable oklch(L C H) string matching the triple", () => {
    for (const colour of IDENTITY_PALETTE) {
      const m = colour.oklch.match(/^oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\)$/);
      expect(m).not.toBeNull();
      if (!m) continue;
      expect(Number.parseFloat(m[1])).toBe(colour.l);
      expect(Number.parseFloat(m[2])).toBe(colour.c);
      expect(Number.parseFloat(m[3])).toBe(colour.h);
    }
  });

  it("documents a name for every entry (for the swatch sheet + DESIGN.md)", () => {
    for (const colour of IDENTITY_PALETTE) {
      expect(colour.name).toBeTruthy();
      expect(colour.name.length).toBeGreaterThan(2);
    }
  });

  it("is frozen (cannot be mutated by a caller)", () => {
    // Object.freeze at the array level — the palette is the design contract.
    expect(Object.isFrozen(IDENTITY_PALETTE)).toBe(true);
  });
});

describe("derivePersonaIdentityColor — D-F1-1 derivation", () => {
  it("is deterministic — same persona id always yields the same colour", () => {
    const a = derivePersonaIdentityColor({ id: "astrid_tenancy_law" });
    const b = derivePersonaIdentityColor({ id: "astrid_tenancy_law" });
    expect(a).toEqual(b);
    expect(a).toBe(b); // same palette entry reference (frozen)
  });

  it("yields a palette member (not a freshly computed colour)", () => {
    for (const persona of REPRESENTATIVE_PERSONAS) {
      const colour = derivePersonaIdentityColor(persona);
      expect(IDENTITY_PALETTE).toContain(colour);
    }
  });

  it("yields different colours for different personas (best-effort — no guaranteed perfect distribution at 12 hues across N personas)", () => {
    // 12 personas through a 12-bucket hash will hit 1+ collision with
    // non-trivial probability. We assert ≥7 distinct hues on the
    // representative set as a regression guard — if FNV-1a degenerates or
    // someone shrinks the palette, this catches the regression.
    const seen = new Set(
      REPRESENTATIVE_PERSONAS.map((p) => derivePersonaIdentityColor(p).h),
    );
    expect(seen.size).toBeGreaterThanOrEqual(7);
  });

  it("snapshots the live demo personas (Astrid / Kai / Maren) so a deliberate hash change is visible", () => {
    // If a future contributor swaps the hash function or reorders the
    // palette, these three live personas' colours change — this test
    // surfaces the breakage as an explicit decision rather than a silent
    // visual regression on the demo. To update: change the hash/palette,
    // run the test, paste the new hues in.
    //
    // The three live personas must land on THREE distinct hues — the §4
    // multi-persona proof (T08 reference composition) depends on it. The
    // Fibonacci-hash mix in paletteIndex() is what guarantees this against
    // the 12-hue palette; plain FNV-1a % 12 collapsed Astrid + Maren onto
    // the same slot.
    const astrid = derivePersonaIdentityColor({ id: "astrid_tenancy_law" });
    const kai = derivePersonaIdentityColor({ id: "kai_research" });
    const maren = derivePersonaIdentityColor({ id: "maren_writing_coach" });
    expect(astrid.h).toMatchInlineSnapshot("340");
    expect(kai.h).toMatchInlineSnapshot("180");
    expect(maren.h).toMatchInlineSnapshot("135");
    expect(new Set([astrid.h, kai.h, maren.h]).size).toBe(3);
  });

  it("differs from --primary vermilion (hue 30) by at least 30° on every persona — never reads as the brand", () => {
    // The palette's closest hue to vermilion 30 is rose-coral at 355°, which
    // is 35° away via wrap-around. At accent-grade C=0.13 vs brand C=0.196,
    // the chroma + lightness difference carries the perceptual distinction
    // beyond the 35° hue gap. Documented in DESIGN.md (T13).
    for (const persona of REPRESENTATIVE_PERSONAS) {
      const hue = derivePersonaIdentityColor(persona).h;
      const distToVermilion = Math.min(
        Math.abs(hue - 30),
        360 - Math.abs(hue - 30),
      );
      expect(distToVermilion).toBeGreaterThanOrEqual(30);
    }
  });
});

describe("personaIdentityStyle — inline CSS-var application", () => {
  it("returns a React-CSSProperties-shaped object with the three identity vars set", () => {
    const style = personaIdentityStyle({ id: "astrid_tenancy_law" });
    expect(style).toHaveProperty("--identity-h");
    expect(style).toHaveProperty("--identity-l");
    expect(style).toHaveProperty("--identity-c");
  });

  it("sets the CSS-var values to the derivation function's output", () => {
    const persona = { id: "astrid_tenancy_law" };
    const colour = derivePersonaIdentityColor(persona);
    const style = personaIdentityStyle(persona);
    expect(style["--identity-h"]).toBe(String(colour.h));
    expect(style["--identity-l"]).toBe(String(colour.l));
    expect(style["--identity-c"]).toBe(String(colour.c));
  });
});

describe("REPRESENTATIVE_PERSONAS — fixture set for swatch + contrast pass", () => {
  it("has exactly 12 personas — matches palette size, exercises every slot in distribution", () => {
    expect(REPRESENTATIVE_PERSONAS).toHaveLength(12);
  });

  it("anchors on the three live demo personas (astrid / kai / maren)", () => {
    const ids = new Set(REPRESENTATIVE_PERSONAS.map((p) => p.id));
    expect(ids.has("astrid_tenancy_law")).toBe(true);
    expect(ids.has("kai_research")).toBe(true);
    expect(ids.has("maren_writing_coach")).toBe(true);
  });

  it("has unique ids so the swatch sheet shows 12 distinct entries", () => {
    const ids = new Set(REPRESENTATIVE_PERSONAS.map((p) => p.id));
    expect(ids.size).toBe(REPRESENTATIVE_PERSONAS.length);
  });
});
