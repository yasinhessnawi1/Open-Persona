/**
 * Spec F1 T06 — Persona avatar default treatment.
 * D-F1-2 lock: initials-mark in identity-coloured fill (Fraunces lettering
 * inside the circle); falls back to user-supplied avatar_url when present
 * (spec-08's field — the override path).
 *
 * Why initials over identicon (rejected):
 *   - Carries persona name + identity colour in ONE mark (an identicon
 *     carries only the colour; the name is recovered from the label beside).
 *   - The avatar_url override transition (letters → portrait) is more
 *     graceful than (abstract identicon → portrait) for visual continuity.
 *   - Fraunces inside the circle is editorial-pole; identicons drift toward
 *     instrument-pole (developer-chic, not editorial-instrument).
 *
 * NOT used in the live app yet — F1 stays in the (reference) compositions
 * (T07–T12). F2 promotes <PersonaAvatar> into persona-card.tsx, the chat
 * identity header, and every persona-touching surface.
 */

import {
  derivePersonaIdentityColor,
  personaIdentityStyle,
} from "@/lib/persona-identity";
import { cn } from "@/lib/utils";

/**
 * Minimum persona shape <PersonaAvatar> needs. Compatible with PersonaSummary
 * (spec-09 API client) and PersonaDetail (the live API row). The fixture
 * fields in the reference compositions also satisfy this shape.
 */
export interface AvatarPersona {
  /** Stable id — drives the deterministic identity-colour derivation. */
  readonly id: string;
  /** Display name — drives the initials-mark. */
  readonly name: string;
  /** Optional user-supplied image override (spec-08 personas.avatar_url). */
  readonly avatar_url?: string | null;
}

export type PersonaAvatarSize = "sm" | "md" | "lg";

const SIZE_CLASSES: Record<PersonaAvatarSize, string> = {
  // sm = list-density (24px). md = chat identity-header (40px).
  // lg = persona-detail hero (64px). Initial sizes from D-F1-2 (T06 spec).
  sm: "size-6 text-[0.6rem]",
  md: "size-10 text-base",
  lg: "size-16 text-2xl",
};

/**
 * Two-letter initials from a name (first letter of first word + first letter
 * of last word). Single-word names get the first two letters. Empty → "?".
 *
 * Mirrors the existing `personaInitials()` in src/lib/persona.ts (used by
 * persona-card.tsx) so both treatments yield identical glyphs for the same
 * name — F2's later promotion swap is visually continuous.
 */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * The persona avatar component.
 *
 * Visual: a circle. If `persona.avatar_url` is set, the circle shows the
 * image (cover-fit, centered). Otherwise it's filled with the persona's
 * derived identity colour with the persona's initials in Fraunces centered.
 *
 * The wrapping element sets `--identity-h` / `--identity-l` / `--identity-c`
 * as CSS custom properties via `personaIdentityStyle()`, so descendants
 * (T07's chat composition, T08's persona list, etc.) can reach the same
 * derived colour for surrounding accents (header underline, message
 * left-border) without re-importing the derivation function.
 */
export function PersonaAvatar({
  persona,
  size = "md",
  className,
}: {
  persona: AvatarPersona;
  size?: PersonaAvatarSize;
  className?: string;
}) {
  const colour = derivePersonaIdentityColor(persona);
  const style = personaIdentityStyle(persona);

  if (persona.avatar_url) {
    // The override path — image takes the surface; identity colour stays
    // available via the inline style for descendants to consume.
    return (
      <span
        style={style}
        className={cn(
          "inline-block overflow-hidden rounded-full",
          SIZE_CLASSES[size],
          className,
        )}
      >
        {/* biome-ignore lint/performance/noImgElement: reference compositions
            use fixture data; no Next/Image optimisation pipeline needed. */}
        <img
          src={persona.avatar_url}
          alt=""
          className="size-full object-cover"
        />
      </span>
    );
  }

  return (
    <span
      // The inline style sets background = the derived OKLCH colour AND
      // exports --identity-* for descendants. White-text-on-coloured-fill
      // because L=0.60 + C=0.13 gives ≥3:1 contrast against white (verified
      // in T14 contrast pass). The drop-shadow softens the contrast edge so
      // letters don't read brittle at small sizes.
      style={{ ...style, background: colour.oklch }}
      className={cn(
        "inline-grid place-items-center rounded-full font-heading font-semibold text-white",
        SIZE_CLASSES[size],
        className,
      )}
      role="img"
      aria-label={persona.name}
    >
      {initials(persona.name)}
    </span>
  );
}
