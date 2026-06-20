"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/auth";
import { createApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * Window event signalling the conversation's file set changed (a user upload or
 * a persona-generated artifact) so the Files viewer can re-fetch and stay in
 * sync with the registry. Decoupled — the composer / chat stream dispatch it;
 * the viewer listens. Mirrors the `chat-streaming` decoupling pattern.
 */
export const CONVERSATION_FILES_CHANGED_EVENT = "conversation-files-changed";

/** Fire the change signal (no-op outside the browser). */
export function notifyConversationFilesChanged(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(CONVERSATION_FILES_CHANGED_EVENT));
  }
}

/** One workspace artifact in the conversation's unified file list. */
export type ArtifactItem = components["schemas"]["ArtifactItem"];

export interface ConversationArtifactsState {
  items: ArtifactItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

/**
 * Spec 35 — the conversation's UNIFIED file list. A single Spec-F5 endpoint
 * (`GET /v1/personas/:id/artifacts?conversation_id=`) already returns BOTH the
 * user's uploads (`metadata.source === "upload"`) and the persona's generated
 * artifacts (`"generated"`), so the chat Files viewer needs no merge — it reads
 * this one list and groups by source. Mirrors `useConversationDocuments`'
 * plain useState + fetch shape (D-09-5); fail-soft on transient errors so the
 * chat surface never breaks.
 */
export function useConversationArtifacts(
  personaId: string,
  conversationId: string,
): ConversationArtifactsState {
  const { getToken } = useAuth();
  const [items, setItems] = useState<ArtifactItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const token = useCallback(
    () => getToken(TEMPLATE ? { template: TEMPLATE } : undefined),
    [getToken],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const jwt = await token();
      const client = createApiClient(() => Promise.resolve(jwt));
      // ONE endpoint, both sources: the F5 artifacts walk returns the user's
      // uploads (`metadata.source === "upload"`) AND the persona's generated
      // artifacts (`"generated"`). Documents land at the owner-scoped workspace
      // path the walk visits and carry a `.f5.json` sidecar (source="upload",
      // type="doc"), so they surface natively here — with a ref preview/download
      // resolves. No merge needed.
      const artifactsRes = await client.GET(
        "/v1/personas/{persona_id}/artifacts",
        {
          params: {
            path: { persona_id: personaId },
            query: { conversation_id: conversationId },
          },
        },
      );
      setItems(artifactsRes.data?.items ?? []);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setLoading(false);
    }
  }, [personaId, conversationId, token]);

  // Fetch on mount + on conversation/persona change (so the header count badge
  // reflects this conversation's files without waiting for the panel to open).
  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { items, loading, error, refresh };
}
