/**
 * Spec F1 T05 — Persona identity-colour palette + derivation function.
 * D-F1-1 lock. X-F1-2 exploration output.
 *
 * The persona-identity visual language is THE core problem of F1 (spec §4).
 * Every persona needs to feel like a distinct individual within one coherent
 * interface. This module is how that lands as code:
 *
 *   - IDENTITY_PALETTE: 12 curated OKLCH hues, drawn from two zones of the
 *     hue wheel (greens-to-cyans 88°–220° + indigos-to-roses 245°–360°). The
 *     zones exclude the bands around the brand vermilion --primary (hue 30,
 *     ±25°), tier-mid amber (hue 73, ±15°), and tier-small slate (hue 232,
 *     ±13°) so persona colours never collide with brand or tier signaling.
 *     Lightness L=0.60 and chroma C=0.13 are fixed — accent-grade, harmonious
 *     with vermilion --primary at C=0.196, never out-shouts it.
 *
 *   - derivePersonaIdentityColor(persona): pure, deterministic. Same persona
 *     always yields the same colour with no stored state. Sync (FNV-1a 32-bit
 *     hash) so SSR/RSC/initial render all resolve the colour synchronously
 *     from the persona's stable id (which spec-08 guarantees unique +
 *     permanent).
 *
 * The palette IS the design decision; the hash is a one-liner.
 *
 * Override path: when a persona has avatar_url set (spec-08's field), the
 * <PersonaAvatar> component prefers the image; the derived identity colour
 * stays as the surrounding accent (header underline, message left-border)
 * for visual continuity.
 */

/**
 * An OKLCH colour triple in the curated palette. `name` is documentation for
 * the swatch sheet and contributor reading.
 */
export interface IdentityColor {
  /** Hue in degrees, 0–360. */
  readonly h: number;
  /** Lightness, 0–1. Fixed to 0.60 across the palette. */
  readonly l: number;
  /** Chroma, 0–~0.4. Fixed to 0.13 across the palette (accent-grade). */
  readonly c: number;
  /** OKLCH CSS colour function string. */
  readonly oklch: string;
  /** Documentation label. */
  readonly name: string;
}

/**
 * The curated 12-hue palette. Phase 3 X-F1-2 exploration output, locked by
 * D-F1-1. Hues spaced ~20° apart within each zone; pairwise distance minimum
 * is 15° (between #11 rose and #12 warm rose-coral).
 *
 * If a future contributor wants to tune a specific hue under T14's CVD-sim
 * pass, the structural constraints are: stay outside the exclusion zones
 * (88°–220° + 245°–360°), keep L=0.60 and C=0.13, maintain ≥15° pairwise
 * hue-wheel distance. Documented in DESIGN.md (T13).
 */
export const IDENTITY_PALETTE: readonly IdentityColor[] = Object.freeze([
  {
    h: 90,
    l: 0.6,
    c: 0.13,
    oklch: "oklch(0.6 0.13 90)",
    name: "warm chartreuse",
  },
  { h: 110, l: 0.6, c: 0.13, oklch: "oklch(0.6 0.13 110)", name: "leaf-green" },
  {
    h: 135,
    l: 0.6,
    c: 0.13,
    oklch: "oklch(0.6 0.13 135)",
    name: "forest-green",
  },
  { h: 158, l: 0.6, c: 0.13, oklch: "oklch(0.6 0.13 158)", name: "sage-teal" },
  { h: 180, l: 0.6, c: 0.13, oklch: "oklch(0.6 0.13 180)", name: "teal" },
  { h: 200, l: 0.6, c: 0.13, oklch: "oklch(0.6 0.13 200)", name: "sky-blue" },
  { h: 260, l: 0.6, c: 0.13, oklch: "oklch(0.6 0.13 260)", name: "indigo" },
  { h: 280, l: 0.6, c: 0.13, oklch: "oklch(0.6 0.13 280)", name: "violet" },
  { h: 300, l: 0.6, c: 0.13, oklch: "oklch(0.6 0.13 300)", name: "magenta" },
  {
    h: 320,
    l: 0.6,
    c: 0.13,
    oklch: "oklch(0.6 0.13 320)",
    name: "fuchsia-pink",
  },
  { h: 340, l: 0.6, c: 0.13, oklch: "oklch(0.6 0.13 340)", name: "rose" },
  {
    h: 355,
    l: 0.6,
    c: 0.13,
    oklch: "oklch(0.6 0.13 355)",
    name: "warm rose-coral",
  },
]);

/**
 * FNV-1a 32-bit hash. Pure, sync, deterministic, no dependencies. The bottom
 * half of the derivation: turn an arbitrary UTF-8 string into a 32-bit
 * unsigned integer with reasonable bit-mixing.
 *
 * Algorithm (https://en.wikipedia.org/wiki/Fowler%E2%80%93Noll%E2%80%93Vo_hash_function):
 *   h = 0x811c9dc5
 *   for each byte b in s:
 *     h = (h XOR b) * 16777619 (mod 2^32)
 */
function fnv1a32(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    // 32-bit FNV prime multiply via shift-and-add (avoids JS number-precision
    // loss on the >32-bit intermediate that `h * 16777619` would produce).
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h;
}

/**
 * Map a 32-bit hash into a palette index via the Fibonacci-hash trick.
 *
 * Plain `hash % N` can clump for small representative sets — the three live
 * demo personas (astrid_tenancy_law, kai_research, maren_writing_coach) hit
 * a 2-way collision under plain `fnv1a32 % 12` (Astrid + Maren both land on
 * the same palette slot, which is exactly the visual regression the §4
 * individuality goal cannot afford on the demo screen).
 *
 * The Fibonacci-hash mix (multiply by 2^32 / φ ≈ 0x9E3779B1, then modulo)
 * spreads adjacent / similar-length string hashes much more evenly across
 * any small modulus. Same input → same output; deterministic.
 *
 * Reference: Knuth, TAOCP §6.4; Robin Hood hashing literature.
 */
function paletteIndex(s: string): number {
  // `Math.imul` keeps the multiply in 32-bit space (JS numbers lose precision
  // above 2^53). The `>>> 0` coerces back to an unsigned 32-bit. The modulo
  // is then well-distributed because the high bits of (h × golden) carry the
  // multiplicative mixing.
  return (Math.imul(fnv1a32(s), 0x9e3779b1) >>> 0) % IDENTITY_PALETTE.length;
}

/**
 * Derive a persona's identity colour from its stable id.
 *
 * Pure, deterministic, sync. The persona's id is the only input; outputs are
 * stable across every call site (chat, list, runs, settings, future capability
 * UIs) and across SSR / RSC / client render. No stored state.
 *
 * Same persona → same colour, forever. If the palette is later refined under
 * T14's CVD pass (a single hue shifted by ≤10°, or L/C by ±0.05), the persona
 * → palette-index mapping does NOT change (the structure is what's locked);
 * only the rendered hue for that one palette slot moves slightly.
 */
export function derivePersonaIdentityColor(persona: {
  id: string;
}): IdentityColor {
  return IDENTITY_PALETTE[paletteIndex(persona.id)];
}

/**
 * Build an inline `style` object that sets the `--identity-h` / `--identity-l`
 * / `--identity-c` CSS custom properties for a persona. Components consume
 * these via `oklch(var(--identity-l) var(--identity-c) var(--identity-h))`.
 *
 * Returned type is `React.CSSProperties`-compatible. Wrapped via
 * `style={personaIdentityStyle(persona)}` on a parent; descendants then use
 * the identity colour without needing to import the derivation function.
 *
 * Example:
 *   <div style={personaIdentityStyle(persona)}>
 *     <span className="border-l-2 border-l-[oklch(var(--identity-l)_var(--identity-c)_var(--identity-h))]" />
 *   </div>
 */
export function personaIdentityStyle(persona: {
  id: string;
}): Record<string, string> {
  const c = derivePersonaIdentityColor(persona);
  return {
    "--identity-h": String(c.h),
    "--identity-l": String(c.l),
    "--identity-c": String(c.c),
  };
}

/**
 * A small representative-persona fixture set for the swatch sheet (T05) and
 * for derivation unit tests. The three live demo personas (Astrid / Kai /
 * Maren) anchor the set; nine invented archetypes span domains the palette
 * should cover. Used by the (reference) swatches page and the contrast/CVD
 * pass (T14).
 */
export interface RepresentativePersona {
  readonly id: string;
  readonly name: string;
  readonly role: string;
}

export const REPRESENTATIVE_PERSONAS: readonly RepresentativePersona[] =
  Object.freeze([
    {
      id: "astrid_tenancy_law",
      name: "Astrid",
      role: "Norwegian tenancy law assistant",
    },
    { id: "kai_research", name: "Kai", role: "Research assistant" },
    { id: "maren_writing_coach", name: "Maren", role: "Writing coach" },
    { id: "oslo_food_guide", name: "Lars", role: "Oslo food guide" },
    { id: "code_review_buddy", name: "Jin", role: "Code review buddy" },
    { id: "philosophy_tutor", name: "Sofia", role: "Philosophy tutor" },
    { id: "nordic_history", name: "Erik", role: "Nordic history guide" },
    { id: "meditation_coach", name: "Anya", role: "Meditation coach" },
    { id: "data_analyst", name: "Priya", role: "Data analyst" },
    { id: "legal_drafter", name: "Marco", role: "Contract drafter" },
    { id: "garden_planner", name: "Birte", role: "Garden planner" },
    { id: "recipe_assistant", name: "Tomás", role: "Recipe assistant" },
  ]);
