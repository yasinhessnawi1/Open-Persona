/**
 * Spec F2 T13 — PersonaIdentityHeader.
 * D-F1-5 composite landed live + D-F2-8 (constraints-cue placement).
 *
 * The D-F1-5 lock: identity-coloured <PersonaAvatar> at the top + persona name
 * in Fraunces with a 1px identity-coloured underline beneath the NAME ONLY
 * (inline-block so the underline hugs the name tightly, not the surrounding
 * row) + role in muted UI text + an optional constraints-cue line gated by
 * `showConstraints` (D-F2-8 lean: chat surface yes, persona-detail yes, list
 * rows no — per-surface decision).
 *
 * Server component (D-F2-3) — props in, JSX out, no hooks/refs. Wrapped with
 * personaIdentityStyle(persona) so descendants reach --identity-h/-l/-c
 * without re-deriving (the underline + future kids consume the colour via
 * oklch(var(--identity-l) var(--identity-c) var(--identity-h))).
 *
 * The identity-colour CSS-var consumption is token-clean (consumes F1's
 * --identity-* scaffolding from globals.css `@theme inline`). The 1px
 * underline `borderBottomWidth` value is a positional rule, not a design
 * value (the design choice — "1px identity-coloured underline" — is the F1
 * D-F1-5 lock; "1px" is the implementation of that lock).
 *
 * F1's `<PersonaAvatar>` provides the avatar; T13 composes it for the
 * identity header role at the requested size (defaults to `md` — 40px).
 */

import {
  type AvatarPersona,
  PersonaAvatar,
  type PersonaAvatarSize,
} from "@/components/persona/persona-avatar";
import {
  derivePersonaIdentityColor,
  personaIdentityStyle,
} from "@/lib/persona-identity";
import { cn } from "@/lib/utils";

/**
 * The persona shape <PersonaIdentityHeader> consumes. Extends AvatarPersona
 * (id + name + avatar_url for the avatar) with role + optional constraint.
 */
export interface IdentityHeaderPersona extends AvatarPersona {
  /** Display role — rendered beneath the name in muted UI text. */
  readonly role: string;
  /**
   * Optional constraint cue — typically the first constraint from the
   * persona's YAML, rendered as a single-line muted hint when
   * `showConstraints` is true. The header keeps this terse; the full
   * constraints list lives on the persona detail page.
   */
  readonly constraint?: string;
}

export type PersonaIdentityHeaderSize = PersonaAvatarSize;

/**
 * <PersonaIdentityHeader persona showConstraints? size? className?>
 *
 * - `persona`: required. Provides id (drives identity colour), name, role,
 *   optional constraint, optional avatar_url.
 * - `showConstraints` (D-F2-8): default false. Pass true on chat header +
 *   persona detail; leave false on list rows.
 * - `size`: matches <PersonaAvatar>'s sizing — `sm` (24px) / `md` (40px) /
 *   `lg` (64px). Default `md`.
 * - `className`: extends the outer flex container.
 */
export function PersonaIdentityHeader({
  persona,
  showConstraints = false,
  size = "md",
  className,
}: {
  persona: IdentityHeaderPersona;
  showConstraints?: boolean;
  size?: PersonaIdentityHeaderSize;
  className?: string;
}) {
  const colour = derivePersonaIdentityColor(persona);
  const style = personaIdentityStyle(persona);

  return (
    <div
      style={style}
      className={cn("flex items-center gap-3", className)}
      data-slot="persona-identity-header"
    >
      <PersonaAvatar persona={persona} size={size} />
      <div className="min-w-0">
        {/*
         * The D-F1-5 underline: 1px identity-coloured `border-bottom` on an
         * inline-block <span> wrapping JUST the name — so the underline hugs
         * the glyphs, not the full row. The colour resolves through the
         * derived persona identity colour (consumes F1's --identity-*
         * scaffolding via the wrapper's personaIdentityStyle).
         */}
        <span
          className="type-heading inline-block leading-tight"
          style={{ borderBottom: `1px solid ${colour.oklch}` }}
          data-slot="persona-identity-name"
        >
          {persona.name}
        </span>
        <p
          className="type-ui truncate text-muted-foreground"
          data-slot="persona-identity-role"
        >
          {persona.role}
        </p>
        {showConstraints && persona.constraint ? (
          <p
            className="type-caption mt-1 truncate text-muted-foreground"
            data-slot="persona-identity-constraint"
          >
            {persona.constraint}
          </p>
        ) : null}
      </div>
    </div>
  );
}
