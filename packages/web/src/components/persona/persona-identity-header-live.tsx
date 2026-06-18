"use client";

import {
  type IdentityHeaderPersona,
  PersonaIdentityHeader,
  type PersonaIdentityHeaderSize,
} from "@/components/persona/persona-identity-header";
import { usePersonaAvatarPoll } from "@/lib/hooks/use-persona-avatar-poll";

/**
 * Client wrapper around <PersonaIdentityHeader> that bounded-polls for the
 * persona's auto-generated `avatar_url` (async-persona-create).
 *
 * After create, `POST /v1/personas` returns immediately with `avatar_url=null`,
 * so the server-rendered header shows F1's default initials-mark. A server-side
 * background task generates the avatar (and picks a voice) a few seconds later.
 * This wrapper polls `GET /v1/personas/{id}` (strictly bounded; cancel-on-
 * unmount — see usePersonaAvatarPoll) and, the moment `avatar_url` populates,
 * re-renders the header so the avatar swaps in gracefully (letters → portrait).
 *
 * When the server already provided an avatar_url (an existing persona, or a
 * builder-supplied avatar) this no-ops the poll and renders exactly as the plain
 * server header would. Identical props to <PersonaIdentityHeader> minus the
 * avatar_url, which this owns.
 */
export function PersonaIdentityHeaderLive({
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
  const avatarUrl = usePersonaAvatarPoll(
    persona.id,
    persona.avatar_url ?? null,
  );

  return (
    <PersonaIdentityHeader
      persona={{ ...persona, avatar_url: avatarUrl }}
      showConstraints={showConstraints}
      size={size}
      className={className}
    />
  );
}
