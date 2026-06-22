"use client";

import { useCallback, useEffect, useRef } from "react";
import { useAuth } from "@/auth";
import type { AuthoringDraft } from "@/lib/api";
import { ApiError, readRateLimit } from "@/lib/api/client";
import { consumeSSE } from "@/lib/sse";
import { parseAuthorEvent } from "@/lib/sse-types";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * Per-call streaming handlers. `onChunk` paints the forming-text preview as the
 * draft streams; `onRetry` flags the visible validation-repair re-stream. Both
 * optional — a caller that only wants the terminal draft can omit them and
 * `await` the resolved `AuthoringDraft` exactly as the old blocking hook did.
 */
export interface AuthorStreamHandlers {
  onChunk?: (delta: string) => void;
  onRetry?: (reason: string) => void;
}

/**
 * The authoring seam (spec 10 D-10-2 + spec P0 streaming). `POST
 * /v1/personas/author` and `/author/refine` now SSE-STREAM the draft: the hook
 * consumes the stream via the shared {@link consumeSSE} helper (D-P0-sse-reuse),
 * surfaces `chunk`/`retry` frames through `handlers`, and resolves with the
 * terminal `draft` payload — the same validated `AuthoringDraft` the blocking
 * path returned (the contract is unchanged, D-10-6). No persona row is created;
 * the wizard reviews/refines, then saves via `POST /v1/personas`.
 *
 * Cancel-safety (D-P0-abort-on-unmount): the hook owns one `AbortController`,
 * aborts it on unmount, and supersedes any in-flight stream when a new call
 * starts — so navigating away aborts the upstream request and the server
 * deducts nothing. An aborted stream rejects with an `AbortError`; callers that
 * abort deliberately should ignore it (`err.name === "AbortError"`).
 */
export function useAuthor() {
  const { getToken } = useAuth();
  const token = useCallback(
    () => getToken(TEMPLATE ? { template: TEMPLATE } : undefined),
    [getToken],
  );
  const controllerRef = useRef<AbortController | null>(null);

  // Abort any in-flight authoring stream when the consumer unmounts
  // (D-P0-abort-on-unmount): cancels the upstream request → no server deduct.
  useEffect(() => () => controllerRef.current?.abort(), []);

  const stream = useCallback(
    async (
      path: "/v1/personas/author" | "/v1/personas/author/refine",
      body: Record<string, unknown>,
      handlers?: AuthorStreamHandlers,
    ): Promise<AuthoringDraft> => {
      // A new stream supersedes any prior in-flight one (abort-prior-on-new-call).
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      const jwt = await token();
      let draft: AuthoringDraft | null = null;
      for await (const raw of consumeSSE(`${API}${path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${jwt}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      })) {
        const ev = parseAuthorEvent(raw);
        if (!ev) continue;
        if (ev.event === "chunk") handlers?.onChunk?.(ev.data.delta);
        else if (ev.event === "retry") handlers?.onRetry?.(ev.data.reason);
        else if (ev.event === "draft") draft = ev.data;
        // `done` closes the stream — nothing more to read.
      }

      // Graceful degrade (acceptance #4): the stream ended without a terminal
      // draft (dropped before completion). Surface a typed error so the caller
      // can retry — there is NO separate REST fallback (D-P0-sse-primary).
      if (draft === null) {
        throw new ApiError(
          0,
          { error: "authoring_stream_incomplete" },
          readRateLimit(new Headers()),
        );
      }
      return draft;
    },
    [token],
  );

  const author = useCallback(
    (
      description: string,
      handlers?: AuthorStreamHandlers,
    ): Promise<AuthoringDraft> =>
      stream("/v1/personas/author", { description }, handlers),
    [stream],
  );

  const refine = useCallback(
    (
      args: {
        currentYaml: string;
        question: string;
        answer: string;
        round: number;
      },
      handlers?: AuthorStreamHandlers,
    ): Promise<AuthoringDraft> =>
      stream(
        "/v1/personas/author/refine",
        {
          current_yaml: args.currentYaml,
          question: args.question,
          answer: args.answer,
          round: args.round,
        },
        handlers,
      ),
    [stream],
  );

  /** Abort the current in-flight stream (e.g. an explicit user cancel). */
  const abort = useCallback(() => controllerRef.current?.abort(), []);

  return { author, refine, abort };
}
