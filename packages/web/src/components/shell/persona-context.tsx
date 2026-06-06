"use client";

/**
 * Spec F2 T19 — persona/conversation context provider.
 *
 * Lets deeper components (chat composer, run viewer, persona-aware sidebar
 * cues) read the active persona without prop-drilling through every route
 * layer. Routes that know their persona wrap content with <PersonaProvider
 * persona={...}>; consumers call usePersona() to read.
 *
 * For F2 v0.1, the context shape is minimal — just the active persona (with
 * the identity fields needed by F2 components). A future spec can extend
 * with conversation id, active run state, etc.
 *
 * Outside a <PersonaProvider>, usePersona() returns null — that's the route
 * doesn't have a persona context (e.g., /personas list, /settings).
 */

import { createContext, type ReactNode, useContext } from "react";
import type { AvatarPersona } from "@/components/persona/persona-avatar";

export interface ActivePersona extends AvatarPersona {
  /** Display role — present in detail/chat contexts. */
  readonly role?: string;
}

const PersonaContext = createContext<ActivePersona | null>(null);

export function PersonaProvider({
  persona,
  children,
}: {
  persona: ActivePersona | null;
  children: ReactNode;
}) {
  return (
    <PersonaContext.Provider value={persona}>
      {children}
    </PersonaContext.Provider>
  );
}

/**
 * Read the active persona from the surrounding <PersonaProvider>. Returns
 * null when no provider is present (the route doesn't have a persona —
 * settings, persona list, etc.).
 */
export function usePersona(): ActivePersona | null {
  return useContext(PersonaContext);
}
