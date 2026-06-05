/**
 * Spec F1 T05 — The X-F1-2 persona identity-colour swatch sheet.
 *
 * Renders the curated 12-hue IDENTITY_PALETTE alongside the 12 representative
 * personas (Astrid + Kai + Maren anchors + 9 invented archetypes). Each cell
 * shows the derived colour applied to an avatar-shape, the persona name + role,
 * and the OKLCH triple — so a human eye can verify:
 *   (a) the palette harmonises with vermilion --primary,
 *   (b) Astrid + Kai + Maren read as distinctly different individuals,
 *   (c) the palette stays accent-grade (never wash-grade),
 *   (d) the dark-mode swap preserves the language (toggle theme to verify),
 *   (e) every persona's identity colour is unmistakable but never dominant.
 *
 * THE proof for §11 #4 (deterministic + distinct) and the load-bearing
 * artifact for §11 #5 (multi-persona visual language). T14 runs the CVD sim.
 */
import {
  derivePersonaIdentityColor,
  IDENTITY_PALETTE,
  personaIdentityStyle,
  REPRESENTATIVE_PERSONAS,
} from "@/lib/persona-identity";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export default function SwatchSheet() {
  return (
    <div className="space-y-12">
      <header className="space-y-2">
        <p className="type-caption text-muted-foreground">
          T05 · X-F1-2 · D-F1-1
        </p>
        <h1 className="type-display">Persona identity-colour swatches</h1>
        <p className="type-body text-muted-foreground max-w-prose">
          The curated 12-hue palette, applied to 12 representative personas via
          the FNV-1a derivation. The palette IS the design decision; the hash is
          a one-liner. Switch the OS theme to verify the dark-mode swap
          preserves identity without losing warmth.
        </p>
      </header>

      <section className="space-y-4">
        <h2 className="type-heading">
          The curated palette — 12 hues, L=0.60, C=0.13
        </h2>
        <p className="type-body text-muted-foreground max-w-prose">
          Hues drawn from two zones of the OKLCH wheel: greens-to-cyans
          (88°–220°) + indigos-to-roses (245°–360°). Excludes the bands around
          the brand vermilion (hue 30, ±25°), tier-mid amber (hue 73, ±15°), and
          tier-small slate (hue 232, ±13°) so persona colours never collide with
          brand or tier signaling.
        </p>
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6">
          {IDENTITY_PALETTE.map((colour) => (
            <div
              key={colour.h}
              className="border-border space-y-2 rounded-lg border p-3"
            >
              <div
                aria-hidden
                className="aspect-square w-full rounded-md"
                style={{ background: colour.oklch }}
              />
              <div className="space-y-0.5">
                <p className="type-ui text-foreground font-medium">
                  {colour.name}
                </p>
                <p className="type-caption text-muted-foreground">
                  hue {colour.h}°
                </p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="type-heading">
          The 12 representative personas — derived identity
        </h2>
        <p className="type-body text-muted-foreground max-w-prose">
          Each persona gets a deterministic identity colour from its stable id.
          Astrid, Kai, and Maren are the live demo personas; the rest are
          invented archetypes spanning domains the palette should serve. The §4
          individuality-within-coherence test: do these 12 read as twelve
          distinct individuals in one coherent product, or as a fruit-salad?
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {REPRESENTATIVE_PERSONAS.map((persona) => {
            const colour = derivePersonaIdentityColor(persona);
            return (
              <div
                key={persona.id}
                style={personaIdentityStyle(persona)}
                className="border-border bg-card flex items-center gap-4 rounded-lg border p-4"
              >
                <div
                  aria-hidden
                  className="grid size-12 shrink-0 place-items-center rounded-full"
                  style={{ background: colour.oklch }}
                >
                  <span className="type-heading text-base text-white drop-shadow-sm">
                    {initials(persona.name)}
                  </span>
                </div>
                <div className="min-w-0 space-y-0.5">
                  <p className="type-ui text-foreground truncate font-medium">
                    {persona.name}
                  </p>
                  <p className="type-caption text-muted-foreground truncate">
                    {persona.role}
                  </p>
                  <p className="type-caption text-muted-foreground/70 truncate">
                    {colour.name} · hue {colour.h}°
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="border-border space-y-3 rounded-lg border border-dashed p-5">
        <h2 className="type-heading">Reading the proof</h2>
        <ul className="type-body text-muted-foreground space-y-2 pl-5 marker:text-foreground/40 list-disc">
          <li>
            <strong className="text-foreground">Individuality:</strong> Astrid /
            Kai / Maren should feel like three different people. They are
            visually distinguishable at a glance from the avatar fill alone.
          </li>
          <li>
            <strong className="text-foreground">Harmony:</strong> Every
            persona's avatar reads warm against the paper base; none clashes
            with vermilion <code className="type-code">--primary</code>.
          </li>
          <li>
            <strong className="text-foreground">Accent-grade:</strong> Persona
            colours are visible but not the heaviest thing on screen. The
            vermilion (`--primary` / `--tier-frontier`) stays the brightest
            accent in the room.
          </li>
          <li>
            <strong className="text-foreground">Dark-mode swap:</strong> Toggle
            the OS to dark. The persona's identity colour stays the same hue
            (Astrid is still Astrid); only the surrounding paper/ink inverts.
          </li>
          <li>
            <strong className="text-foreground">CVD safety:</strong> T14
            verifies under deuteranopia / protanopia / tritanopia sim. Any
            palette adjustments stay within the structural constraints (zones +
            L=0.60 + C=0.13 + ≥15° pairwise distance).
          </li>
        </ul>
      </section>
    </div>
  );
}
