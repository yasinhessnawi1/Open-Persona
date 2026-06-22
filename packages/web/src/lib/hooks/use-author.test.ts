import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAuthor } from "./use-author";

/**
 * Spec P0 / T4 — `useAuthor` SSE consumption + cancel-safety.
 *
 * Drives the hook against a patched `fetch` returning hand-built SSE streams
 * (the same `chunk`/`retry`/`draft`/`done` vocabulary the route emits). Asserts:
 * chunks surface through `onChunk`, the terminal `draft` resolves the promise,
 * `retry` surfaces through `onRetry`, a stream with no terminal draft rejects
 * (graceful degrade), the request body/auth shape is preserved, and an unmount
 * aborts the in-flight request (D-P0-abort-on-unmount).
 */

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: () => Promise.resolve("test-jwt-token") }),
}));

const DRAFT = {
  yaml: 'schema_version: "1.0"\nidentity:\n  name: Lex\n',
  questions: [{ section: "identity", question: "Which area?" }],
  prompt_version: "v3",
  errors: null,
};

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function sseResponse(frames: string[]): Response {
  return new Response(
    new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode(frames.join("")));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

interface Captured {
  url: string;
  body: string;
  headers: Record<string, string>;
  signal?: AbortSignal;
}

function installFetch(responder: (init?: RequestInit) => Response): {
  captured: Captured[];
  restore: () => void;
} {
  const captured: Captured[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = vi.fn(
    async (url: string | URL | Request, init?: RequestInit) => {
      captured.push({
        url: typeof url === "string" ? url : url.toString(),
        body: typeof init?.body === "string" ? init.body : "",
        headers: (init?.headers as Record<string, string>) ?? {},
        signal: init?.signal ?? undefined,
      });
      return responder(init);
    },
  ) as unknown as typeof fetch;
  return {
    captured,
    restore: () => {
      globalThis.fetch = original;
    },
  };
}

describe("useAuthor — SSE streaming", () => {
  let restore: () => void;
  afterEach(() => restore?.());

  it("streams chunk deltas through onChunk and resolves with the terminal draft", async () => {
    ({ restore } = installFetch(() =>
      sseResponse([
        frame("chunk", { delta: "sche", is_final: false }),
        frame("chunk", { delta: "ma", is_final: false }),
        frame("draft", DRAFT),
        frame("done", {}),
      ]),
    ));
    const { result } = renderHook(() => useAuthor());
    const chunks: string[] = [];
    const draft = await result.current.author("a lawyer", {
      onChunk: (d) => chunks.push(d),
    });
    expect(chunks).toEqual(["sche", "ma"]);
    expect(draft).toEqual(DRAFT);
  });

  it("surfaces the visible validation re-stream through onRetry", async () => {
    ({ restore } = installFetch(() =>
      sseResponse([
        frame("chunk", { delta: "bad", is_final: false }),
        frame("retry", { reason: "validation" }),
        frame("chunk", { delta: "good", is_final: false }),
        frame("draft", DRAFT),
        frame("done", {}),
      ]),
    ));
    const { result } = renderHook(() => useAuthor());
    const retries: string[] = [];
    await result.current.author("x", { onRetry: (r) => retries.push(r) });
    expect(retries).toEqual(["validation"]);
  });

  it("rejects when the stream ends without a terminal draft (graceful degrade)", async () => {
    ({ restore } = installFetch(() =>
      sseResponse([
        frame("chunk", { delta: "x", is_final: false }),
        frame("done", {}),
      ]),
    ));
    const { result } = renderHook(() => useAuthor());
    await expect(result.current.author("x")).rejects.toMatchObject({
      code: "authoring_stream_incomplete",
    });
  });

  it("POSTs {description} with the Bearer JWT to /author", async () => {
    const { captured, restore: r } = installFetch(() =>
      sseResponse([frame("draft", DRAFT), frame("done", {})]),
    );
    restore = r;
    const { result } = renderHook(() => useAuthor());
    await result.current.author("a lawyer");
    expect(captured[0].url).toContain("/v1/personas/author");
    expect(JSON.parse(captured[0].body)).toEqual({ description: "a lawyer" });
    expect(captured[0].headers.Authorization).toBe("Bearer test-jwt-token");
    expect(captured[0].headers["Content-Type"]).toBe("application/json");
  });

  it("refine POSTs the snake_case body to /author/refine", async () => {
    const { captured, restore: r } = installFetch(() =>
      sseResponse([frame("draft", DRAFT), frame("done", {})]),
    );
    restore = r;
    const { result } = renderHook(() => useAuthor());
    await result.current.refine({
      currentYaml: "schema_version: '1.0'",
      question: "Which area?",
      answer: "Tenancy.",
      round: 1,
    });
    expect(captured[0].url).toContain("/v1/personas/author/refine");
    expect(JSON.parse(captured[0].body)).toEqual({
      current_yaml: "schema_version: '1.0'",
      question: "Which area?",
      answer: "Tenancy.",
      round: 1,
    });
  });

  it("aborts the in-flight request on unmount (D-P0-abort-on-unmount)", async () => {
    // A stream that never closes keeps the request in-flight so we can observe
    // the abort fire on unmount.
    const { captured, restore: r } = installFetch(
      () =>
        new Response(new ReadableStream({ start() {} }), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
    );
    restore = r;
    const { result, unmount } = renderHook(() => useAuthor());
    // Don't await — it stays pending; swallow the eventual AbortError rejection.
    result.current.author("x").catch(() => {});
    await waitFor(() => expect(captured.length).toBeGreaterThan(0));
    expect(captured[0].signal?.aborted).toBe(false);
    unmount();
    expect(captured[0].signal?.aborted).toBe(true);
  });
});
