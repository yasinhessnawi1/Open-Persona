/**
 * Sidebar data model + the recency ranking shared with the dashboard.
 *
 * The richer app-sidebar surfaces two derived lists from data the app already
 * fetches (no new endpoint, no recency schema):
 *
 *   - PERSONAS: the most-recently-*used* personas (then most-recently-created),
 *     rendered as a compact avatar rail for fast access.
 *   - MESSAGES: the caller's conversations (already `updated_at DESC`), rendered
 *     as a chat-app list. `GET /v1/conversations` returns a summary only
 *     (id / persona_id / title / timestamps) — there is no last-message-author
 *     or message-preview field on the list row, so the row's brief line is the
 *     conversation `title` (the human-readable thread label) and the title line
 *     is the persona name. Surfacing the literal last message would require an
 *     N+1 fetch of `GET /v1/conversations/:id` per row, which the list view
 *     deliberately avoids.
 *
 * `rankPersonasByRecency` is the same derivation `src/app/page.tsx` performs
 * inline for the dashboard — extracted here so both surfaces stay coherent and
 * the ranking is unit-testable in isolation.
 */

import type { AvatarPersona } from "@/components/persona/persona-avatar";

/** Minimum persona shape the sidebar + ranking need (a `PersonaSummary` superset). */
export interface SidebarPersona extends AvatarPersona {
  readonly role: string;
  readonly created_at: string;
}

/** Minimum conversation shape (a `ConversationSummary`). */
export interface SidebarConversationInput {
  readonly id: string;
  readonly persona_id: string;
  readonly title: string;
  readonly updated_at: string;
}

/** A resolved message row: a conversation joined to its persona (if known). */
export interface SidebarConversation {
  readonly id: string;
  readonly title: string;
  readonly updated_at: string;
  readonly persona: SidebarPersona | null;
}

/** The serialisable bundle the server shell hands to the client sidebar. */
export interface SidebarData {
  readonly personas: readonly SidebarPersona[];
  readonly conversations: readonly SidebarConversation[];
}

/**
 * Rank personas "most recently used first" using only existing data.
 *
 * `conversations` is assumed `updated_at DESC` (the API guarantees this), so the
 * first appearance of each `persona_id` marks its most-recent activity. Personas
 * never talked to fall to the tail, most-recently-created first. Pure + stable.
 */
export function rankPersonasByRecency(
  personas: readonly SidebarPersona[],
  conversations: readonly SidebarConversationInput[],
): readonly SidebarPersona[] {
  const byId = new Map(personas.map((p) => [p.id, p]));
  const used: SidebarPersona[] = [];
  const seen = new Set<string>();
  for (const c of conversations) {
    const p = byId.get(c.persona_id);
    if (p && !seen.has(p.id)) {
      seen.add(p.id);
      used.push(p);
    }
  }
  const unused = personas
    .filter((p) => !seen.has(p.id))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  return [...used, ...unused];
}

/**
 * Resolve conversation summaries into message rows joined to their persona.
 * Order is preserved (the API already returns `updated_at DESC`).
 */
export function resolveConversations(
  conversations: readonly SidebarConversationInput[],
  personas: readonly SidebarPersona[],
): readonly SidebarConversation[] {
  const byId = new Map(personas.map((p) => [p.id, p]));
  return conversations.map((c) => ({
    id: c.id,
    title: c.title,
    updated_at: c.updated_at,
    persona: byId.get(c.persona_id) ?? null,
  }));
}
