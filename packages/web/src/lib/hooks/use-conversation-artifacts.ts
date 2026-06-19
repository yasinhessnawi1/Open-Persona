"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/auth";
import { createApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/** Best-effort media type for a document `format` (drives the chip glyph). */
const MEDIA_TYPE_BY_FORMAT: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  csv: "text/csv",
  txt: "text/plain",
  md: "text/markdown",
  code: "text/plain",
};

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
      // Two sources, each fail-soft: generated + image-upload artifacts from the
      // F5 endpoint, and the conversation's DOCUMENTS. Documents are merged here
      // as a stopgap because they're written to a workspace path the artifacts
      // walk doesn't visit (missing owner segment — backend fix pending), so they
      // never appear in the artifact list on their own.
      const [artifactsRes, docsRes] = await Promise.all([
        client.GET("/v1/personas/{persona_id}/artifacts", {
          params: {
            path: { persona_id: personaId },
            query: { conversation_id: conversationId },
          },
        }),
        client.GET("/v1/conversations/{conversation_id}/documents", {
          params: { path: { conversation_id: conversationId } },
        }),
      ]);
      const artifacts = artifactsRes.data?.items ?? [];
      const docItems: ArtifactItem[] = (docsRes.data ?? []).map((d) => ({
        ref: d.workspace_path,
        size_bytes: d.size_bytes ?? 0,
        media_type: MEDIA_TYPE_BY_FORMAT[d.format] ?? "text/plain",
        metadata: {
          source: "upload",
          type: "doc",
          producing_spec: "14",
          conversation_id: conversationId,
          created_at: new Date(0).toISOString(),
          original_name: d.filename,
        },
      }));
      const seen = new Set(docItems.map((d) => d.ref));
      setItems([...docItems, ...artifacts.filter((a) => !seen.has(a.ref))]);
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
