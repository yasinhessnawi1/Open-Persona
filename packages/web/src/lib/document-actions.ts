/**
 * F3 (T14) — document remove action.
 *
 * Thin wrapper around `DELETE /v1/conversations/:id/documents/:ref`
 * (Spec 14 T18). Idempotent at the API layer (204 on already-removed),
 * so the caller doesn't need to special-case "remove twice."
 *
 * The caller (T19's ChatWindow wiring) calls
 * `useConversationDocuments.removeOptimistic(docRef)` BEFORE this
 * function returns so the chip disappears immediately; on failure the
 * caller can `refresh()` to reconcile.
 */

import type { TokenGetter } from "@/lib/api/client";
import { ApiError, createApiClient } from "@/lib/api/client";

export async function removeDocument(
  conversationId: string,
  docRef: string,
  getToken: TokenGetter,
): Promise<void> {
  const jwt = await getToken();
  const client = createApiClient(() => Promise.resolve(jwt));
  const result = await client.DELETE(
    "/v1/conversations/{conversation_id}/documents/{doc_ref}",
    {
      params: {
        path: { conversation_id: conversationId, doc_ref: docRef },
      },
    },
  );
  if (result.error !== undefined) {
    // openapi-fetch parses non-2xx into `error`. 404 means the doc was
    // already gone (cascade-delete on conversation, etc.); not a user-
    // visible failure. Surface 5xx + 422 as ApiError so the caller can
    // refresh + toast.
    const status = result.response.status;
    if (status === 404) return;
    throw new ApiError(
      status,
      result.error as {
        error?: string;
        detail?: unknown;
        context?: Record<string, string>;
      },
      {
        limit: null,
        remaining: null,
        reset: null,
        retryAfter: null,
      },
    );
  }
}
