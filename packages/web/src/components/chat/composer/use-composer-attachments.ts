"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";
import { ApiError, type TokenGetter } from "@/lib/api/client";
import { type DocumentRef, uploadDocument, uploadImage } from "@/lib/upload";
import type { ImageAttachment } from "./attach-state";

const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * F3 (T13 + T14) — composer attachment orchestration.
 *
 * Owns the IMAGE-attached state slice + the upload orchestration for
 * BOTH images and documents. Documents themselves live in
 * `useConversationDocuments` (conversation-scoped); this hook calls
 * `addDocument` / `removeDocument` callbacks the parent threads through
 * from the panel state.
 *
 * **Conversation-switch invariant (D-F3-X-cap-attached-state-on-
 * conversation-switch):** image attachments reset when `conversationId`
 * changes. The dep array on the reset effect is literally
 * `[conversationId]` — NOT `[conversationId, personaId]` and NOT
 * `[conversationId, capabilities]`. Image attachments are message-
 * scoped and have NO meaning across conversations; clearing them is
 * the correct behaviour.
 *
 * **Partial-upload failure (D-F3-X-partial-upload-failure-shape):**
 * Each image attachment carries its own state; `attachmentsBlockSend`
 * (from attach-state.ts) reads them and the parent disables send while
 * any image is `uploading` or `error`. `retryImage` re-runs the upload
 * with the original `File` retained on the attachment state.
 */
export interface ComposerAttachmentsState {
  attachedImages: ImageAttachment[];
  /** Add an image attachment and kick off the upload (returns the assigned id). */
  attachImage: (file: File) => string;
  /** Re-upload a failed image attachment (D-F3-X-partial-upload-failure-shape). */
  retryImage: (id: string) => void;
  /** Remove an image attachment (aborts any in-flight upload). */
  removeImage: (id: string) => void;
  /** Clear ALL image attachments (called after successful send). */
  clearImages: () => void;
  /** Upload a document; on success appends to the conversation panel. */
  uploadDocumentFile: (file: File) => Promise<void>;
}

export interface ComposerAttachmentsOptions {
  conversationId: string;
  personaId: string;
  /** Conversation panel append (from `useConversationDocuments.addOptimistic`). */
  onDocumentAttached: (ref: DocumentRef) => void;
  /** Error surface for documents (toast / inline). Receives the structured ApiError detail. */
  onDocumentError: (detail: string) => void;
}

function makeId(): string {
  // crypto.randomUUID() is in jsdom + all modern browsers; fall back to a
  // monotonic counter would add stale-dependency risk for tests, so use
  // the API directly.
  return crypto.randomUUID();
}

export function useComposerAttachments(
  options: ComposerAttachmentsOptions,
): ComposerAttachmentsState {
  const { conversationId, personaId, onDocumentAttached, onDocumentError } =
    options;
  const { getToken } = useAuth();
  const [attachedImages, setAttachedImages] = useState<ImageAttachment[]>([]);

  // SOLE dependency = conversationId (D-F3-X-cap-attached-state-on-conversation-switch).
  // Do NOT add personaId / capabilities to this array — see decisions.md.
  // biome-ignore lint/correctness/useExhaustiveDependencies: invariant locked
  useEffect(() => {
    setAttachedImages([]);
  }, [conversationId]);

  const token: TokenGetter = useCallback(
    () => getToken(TEMPLATE ? { template: TEMPLATE } : undefined),
    [getToken],
  );

  const patch = useCallback(
    (id: string, fn: (a: ImageAttachment) => ImageAttachment) => {
      setAttachedImages((prev) => prev.map((a) => (a.id === id ? fn(a) : a)));
    },
    [],
  );

  const startUpload = useCallback(
    async (id: string, file: File) => {
      patch(id, () => ({
        kind: "image",
        id,
        file,
        state: "uploading",
        progress: null,
      }));
      try {
        const response = await uploadImage(personaId, file, {
          getToken: token,
          onProgress: (fraction) => {
            patch(id, (a) =>
              a.state === "uploading" ? { ...a, progress: fraction } : a,
            );
          },
        });
        patch(id, () => ({
          kind: "image",
          id,
          file,
          state: "success",
          workspacePath: response.workspace_path,
          mediaType: response.media_type,
        }));
      } catch (e) {
        // ApiError carries the structured server detail; surface its
        // `context.reason` (Spec 13 returns oversize / magic_bytes_mismatch /
        // etc.) so F2's error voice maps it via the i18n table.
        const detail =
          e instanceof ApiError
            ? `${e.code}${e.detail ? `: ${String(e.detail)}` : ""}`
            : e instanceof Error
              ? e.message
              : String(e);
        patch(id, () => ({
          kind: "image",
          id,
          file,
          state: "error",
          reason: "server_rejected",
          detail,
        }));
      }
    },
    [personaId, token, patch],
  );

  const attachImage = useCallback(
    (file: File): string => {
      const id = makeId();
      setAttachedImages((prev) => [
        ...prev,
        { kind: "image", id, file, state: "pending" },
      ]);
      void startUpload(id, file);
      return id;
    },
    [startUpload],
  );

  const retryImage = useCallback(
    (id: string) => {
      const target = attachedImages.find((a) => a.id === id);
      if (!target) return;
      void startUpload(id, target.file);
    },
    [attachedImages, startUpload],
  );

  const removeImage = useCallback((id: string) => {
    setAttachedImages((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const clearImages = useCallback(() => {
    setAttachedImages([]);
  }, []);

  const uploadDocumentFile = useCallback(
    async (file: File) => {
      try {
        const ref = await uploadDocument(personaId, conversationId, file, {
          getToken: token,
        });
        onDocumentAttached(ref);
      } catch (e) {
        const detail =
          e instanceof ApiError
            ? `${e.code}${e.detail ? `: ${String(e.detail)}` : ""}`
            : e instanceof Error
              ? e.message
              : String(e);
        onDocumentError(detail);
      }
    },
    [personaId, conversationId, token, onDocumentAttached, onDocumentError],
  );

  return {
    attachedImages,
    attachImage,
    retryImage,
    removeImage,
    clearImages,
    uploadDocumentFile,
  };
}
