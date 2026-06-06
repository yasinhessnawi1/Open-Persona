/**
 * Spec F2 T14 — PersonaCard (live, data-driven).
 *
 * Replaces the scaffold's src/components/personas/persona-card.tsx (which had
 * the D-F1-5 violation: a uniform `bg-primary/10 text-primary` avatar fill
 * regardless of persona). The F2 card composes <PersonaAvatar> (F1) so the
 * fill is per-persona derived; Astrid + Kai + Maren now read as three
 * distinct individuals in the list (the §4 multi-persona individuality gate).
 *
 * Server component (D-F2-3) — pure presentational; href + persona props in,
 * Link-wrapped Card out. Composes T04's retokenised <Card> + T06's shadcn
 * <Avatar>-extending F1 <PersonaAvatar>.
 *
 * F1 reference: /reference/personas — Astrid (rose 340°) + Kai (teal 180°) +
 * Maren (forest-green 135°) side-by-side. This is the live equivalent.
 */

import Link from "next/link";
import {
  type AvatarPersona,
  PersonaAvatar,
} from "@/components/persona/persona-avatar";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * The persona shape <PersonaCard> consumes — extends AvatarPersona with the
 * role string. Compatible with PersonaSummary from the generated API client.
 */
export interface PersonaCardPersona extends AvatarPersona {
  /** Display role — rendered beneath the name in muted UI text. */
  readonly role: string;
}

/**
 * <PersonaCard persona href? className?>
 *
 * - `persona`: required. Provides id (drives identity colour), name, role,
 *   optional avatar_url.
 * - `href`: optional. If provided, the card becomes a <Link> to that route;
 *   otherwise rendered as a static panel (useful in static list previews +
 *   the criterion-#11 evidence package).
 * - `className`: extends the outer container.
 */
export function PersonaCard({
  persona,
  href,
  className,
}: {
  persona: PersonaCardPersona;
  href?: string;
  className?: string;
}) {
  const body = (
    <Card
      size="sm"
      className={cn(
        // Hover affordance via T03's retokenised motion: F1 --motion-duration-fast.
        "group/persona-card flex flex-row items-center gap-4 p-4 transition-colors duration-[var(--motion-duration-fast)] hover:bg-accent/40",
        className,
      )}
      data-slot="persona-card"
    >
      <PersonaAvatar persona={persona} size="md" className="shrink-0" />
      <div className="min-w-0">
        <p
          className="type-heading truncate leading-tight"
          data-slot="persona-card-name"
        >
          {persona.name}
        </p>
        <p
          className="type-ui truncate text-muted-foreground"
          data-slot="persona-card-role"
        >
          {persona.role}
        </p>
      </div>
    </Card>
  );

  if (href) {
    return (
      <Link href={href} className="block">
        {body}
      </Link>
    );
  }

  return body;
}
