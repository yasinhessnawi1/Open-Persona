/**
 * F3 — shared multipart upload service (T04).
 *
 * Dispatches by content-type to Spec 13's image branch (returns
 * `ImageRef`) or Spec 14's document branch (returns `DocumentRef`)
 * against the shared `POST /v1/personas/:id/uploads` endpoint
 * (CSA-2 dispatcher). The document branch threads `conversation_id` per
 * D-F3-X-document-attach-conversation-binding.
 *
 * Uses XMLHttpRequest (not `fetch`) so the caller can subscribe to real
 * byte-level upload progress per D-F3-4. The `AbortController` signal
 * cancels in-flight uploads on composer cleanup / conversation-switch
 * (D-F3-X-cap-attached-state-on-conversation-switch).
 *
 * **Store-by-reference (Dominant Concern #4):** this module NEVER
 * base64-encodes the file bytes into a JSON body. The browser uploads
 * raw multipart bytes ONCE to `/uploads`; the returned reference is
 * what eventually lands in `PostMessageRequest.images` (T06).
 */

import type { TokenGetter } from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

export type ImageRef = components["schemas"]["ImageRef"];
export type DocumentRef = components["schemas"]["DocumentRef"];

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Fraction in [0, 1]; null when the underlying request can't report it. */
export type ProgressCallback = (fraction: number | null) => void;

/**
 * Service-returned shape for an image upload — superset of `ImageRef` with
 * `size_bytes` echoed back (the API responds with `{workspace_path,
 * media_type, size_bytes}` per `routes/uploads.py:_handle_image_upload`).
 * The `size_bytes` field is informational only; messages carry just the
 * `ImageRef`.
 */
export interface ImageUploadResponse extends ImageRef {
  size_bytes: number;
}

interface UploadOptions {
  /** Async source of the Clerk JWT (T02 client.ts pattern). */
  getToken: TokenGetter;
  /** Optional upload-progress sink. Called with values in [0, 1]; `null` if size unknown. */
  onProgress?: ProgressCallback;
  /** Optional abort signal (composer unmount, conversation switch). */
  signal?: AbortSignal;
}

/**
 * Upload an image to Spec 13's branch of `/v1/personas/:id/uploads`.
 *
 * On success: resolves with the workspace reference. On non-2xx: throws
 * {@link ApiError} carrying the API's structured error body
 * (e.g. `{error: "image_validation_error", detail: ..., context: {reason, ...}}`).
 *
 * @param personaId    Target persona (RLS-scoped at the API).
 * @param file         Browser `File` — bytes uploaded as multipart, never base64.
 * @param options      Token getter + optional progress + optional abort signal.
 */
export async function uploadImage(
  personaId: string,
  file: File,
  options: UploadOptions,
): Promise<ImageUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const body = await sendMultipart(
    `${BASE_URL}/v1/personas/${encodeURIComponent(personaId)}/uploads`,
    form,
    options,
  );
  return body as ImageUploadResponse;
}

/**
 * Upload a document to Spec 14's branch of `/v1/personas/:id/uploads`.
 *
 * Threads `conversation_id` per D-F3-X-document-attach-conversation-binding.
 * Returns a {@link DocumentRef} the conversation document list (T12)
 * appends optimistically.
 *
 * @param personaId      Target persona.
 * @param conversationId Conversation that scopes the document (required;
 *                       the API returns 422 `conversation_id_required` otherwise).
 * @param file           Browser `File` — uploaded as multipart, never base64.
 * @param options        Token getter + optional progress + optional abort signal.
 */
export async function uploadDocument(
  personaId: string,
  conversationId: string,
  file: File,
  options: UploadOptions,
): Promise<DocumentRef> {
  const form = new FormData();
  form.append("file", file);
  form.append("conversation_id", conversationId);
  const body = await sendMultipart(
    `${BASE_URL}/v1/personas/${encodeURIComponent(personaId)}/uploads`,
    form,
    options,
  );
  return body as DocumentRef;
}

/**
 * Internal: POST a `FormData` body with the Bearer token + optional
 * progress + optional abort. Maps non-2xx → {@link ApiError}; network
 * failure / abort → distinct messages so the UI can branch on intent.
 */
async function sendMultipart(
  url: string,
  form: FormData,
  options: UploadOptions,
): Promise<unknown> {
  // Early-exit if the caller aborted before getToken() resolved (common
  // when the composer unmounts mid-token-fetch on conversation switch).
  if (options.signal?.aborted) {
    const err = new Error("upload aborted");
    err.name = "AbortError";
    throw err;
  }
  const token = await options.getToken();
  return new Promise((resolve, reject) => {
    // Re-check after the await: the signal may have fired during the token
    // fetch. Mirrors the pre-fetch guard for the post-fetch window.
    if (options.signal?.aborted) {
      const err = new Error("upload aborted");
      err.name = "AbortError";
      reject(err);
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url, true);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    // Real byte-level progress (D-F3-4). `lengthComputable` false on some
    // proxies — surface as `null` so the UI shows an indeterminate spinner.
    if (options.onProgress) {
      xhr.upload.onprogress = (ev) => {
        options.onProgress?.(
          ev.lengthComputable && ev.total > 0 ? ev.loaded / ev.total : null,
        );
      };
    }

    xhr.onload = () => {
      let parsed: unknown;
      try {
        parsed = xhr.responseText ? JSON.parse(xhr.responseText) : undefined;
      } catch {
        // Non-JSON 5xx body — surface as ApiError with raw text in detail.
        parsed = { error: "non_json_response", detail: xhr.responseText };
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        options.onProgress?.(1);
        resolve(parsed);
        return;
      }
      reject(
        new ApiError(
          xhr.status,
          (parsed as {
            error?: string;
            detail?: unknown;
            context?: Record<string, string>;
          }) ?? undefined,
          {
            // Rate-limit headers are exposed even on error responses; mirror
            // client.ts readRateLimit shape without re-importing Headers.
            limit: numberOrNull(xhr.getResponseHeader("X-RateLimit-Limit")),
            remaining: numberOrNull(
              xhr.getResponseHeader("X-RateLimit-Remaining"),
            ),
            reset: numberOrNull(xhr.getResponseHeader("X-RateLimit-Reset")),
            retryAfter: numberOrNull(xhr.getResponseHeader("Retry-After")),
          },
        ),
      );
    };

    xhr.onerror = () => reject(new Error("upload network failure"));
    xhr.onabort = () => {
      // Aborted via signal — distinct error so the UI doesn't toast.
      const err = new Error("upload aborted");
      err.name = "AbortError";
      reject(err);
    };

    options.signal?.addEventListener(
      "abort",
      () => {
        xhr.abort();
      },
      { once: true },
    );

    xhr.send(form);
  });
}

function numberOrNull(value: string | null): number | null {
  if (value === null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
