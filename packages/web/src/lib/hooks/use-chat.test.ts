import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ImageRef } from "./use-chat";
import { useChat } from "./use-chat";

/**
 * F3 (T06) — `useChat.send` strangler-fig assertions.
 *
 * Scope: ONLY the request-body shape change (thread `images` into
 * PostMessageRequest body). SSE consumption + RunEvent envelope handling +
 * error-toast routing + reconnect behaviour are F2-tested and remain
 * UNCHANGED — these tests don't re-verify them. The body-shape assertions
 * are the load-bearing structural defence for Concern #4 (store-by-
 * reference end-to-end); T22 follows up with the 1 MB → < 2 KB regression
 * guard at the outer suite level.
 */

// Clerk's useAuth() returns a stub token getter; no real Clerk wiring.
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({
    getToken: () => Promise.resolve("test-jwt-token"),
  }),
}));

interface CapturedRequest {
  url: string;
  body: string;
  headers: Record<string, string>;
}

/**
 * Patch global `fetch` to capture the first POST and return an empty
 * SSE response so the hook unwinds cleanly. We don't need streaming
 * events to verify the request body shape.
 */
function installFetchCapture(): {
  captured: CapturedRequest[];
  restore: () => void;
} {
  const captured: CapturedRequest[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = vi.fn(
    async (url: string | URL | Request, init?: RequestInit) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (init?.method === "POST" && urlStr.includes("/messages")) {
        captured.push({
          url: urlStr,
          body: typeof init.body === "string" ? init.body : "",
          headers: (init.headers as Record<string, string>) ?? {},
        });
      }
      // Empty SSE stream → `for await` loop exits immediately → hook
      // transitions to `streaming: false`. No `done` event needed for
      // body-shape assertions.
      return new Response(new ReadableStream({ start: (c) => c.close() }), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    },
  ) as unknown as typeof fetch;
  return {
    captured,
    restore: () => {
      globalThis.fetch = original;
    },
  };
}

describe("useChat.send — F3 T06 request body shape", () => {
  let restore: () => void;
  const capturedRef = { current: [] as CapturedRequest[] };
  afterEach(() => restore?.());

  it("text-only send: body is {content} ONLY — no `images` key (text path unchanged)", async () => {
    const { captured, restore: r } = installFetchCapture();
    restore = r;
    capturedRef.current = captured;

    const { result } = renderHook(() => useChat("conv_1", []));
    await result.current.send("hello world");
    await waitFor(() => expect(captured.length).toBeGreaterThan(0));

    const body = JSON.parse(captured[0].body);
    expect(body).toEqual({ content: "hello world" });
    // Critical: the `images` key MUST NOT be present (server rejects
    // `images: []` as min_length=1 violation per requests.py:143).
    expect("images" in body).toBe(false);
  });

  it("with attached image: body includes `images: [ref]` exactly preserved", async () => {
    const { captured, restore: r } = installFetchCapture();
    restore = r;

    const ref: ImageRef = {
      workspace_path: "uploads/abc.png",
      media_type: "image/png",
    };
    const { result } = renderHook(() => useChat("conv_1", []));
    await result.current.send("describe this", [ref]);
    await waitFor(() => expect(captured.length).toBeGreaterThan(0));

    const body = JSON.parse(captured[0].body);
    expect(body.content).toBe("describe this");
    expect(body.images).toEqual([ref]);
  });

  it("with 4 attached images (the per-message cap): all 4 thread through in order", async () => {
    const { captured, restore: r } = installFetchCapture();
    restore = r;

    const refs: ImageRef[] = [
      { workspace_path: "uploads/a.png", media_type: "image/png" },
      { workspace_path: "uploads/b.jpeg", media_type: "image/jpeg" },
      { workspace_path: "uploads/c.webp", media_type: "image/webp" },
      { workspace_path: "uploads/d.gif", media_type: "image/gif" },
    ];
    const { result } = renderHook(() => useChat("conv_1", []));
    await result.current.send("compare", refs);
    await waitFor(() => expect(captured.length).toBeGreaterThan(0));

    const body = JSON.parse(captured[0].body);
    expect(body.images).toHaveLength(4);
    expect(body.images).toEqual(refs); // order-preserving
  });

  it("store-by-reference: a simulated 1 MB image attachment produces a < 2 KB body", async () => {
    // Concern #4 structural defence — the API-call-layer mirror of Spec
    // 13's T13 DB-layer regression test. The chat-send body MUST carry
    // only the reference, never inlined base64. The "1 MB" is symbolic:
    // we attach a ref whose workspace_path is short (a realistic
    // workspace path), proving the body size is bounded by reference
    // count, not by image bytes.
    const { captured, restore: r } = installFetchCapture();
    restore = r;

    const ref: ImageRef = {
      workspace_path: "uploads/large-1mb-image-9f8e7d6c5b4a3210.png",
      media_type: "image/png",
    };
    const { result } = renderHook(() => useChat("conv_1", []));
    await result.current.send(
      "what's in this image?",
      [ref, ref, ref, ref], // 4 refs, the max
    );
    await waitFor(() => expect(captured.length).toBeGreaterThan(0));

    expect(captured[0].body.length).toBeLessThan(2 * 1024);
    expect(captured[0].body).not.toContain("base64");
    expect(captured[0].body).not.toContain("data:image");
  });

  it("optimistic user-turn carries `images` so the bubble renders inline", async () => {
    const { restore: r } = installFetchCapture();
    restore = r;

    const ref: ImageRef = {
      workspace_path: "uploads/x.png",
      media_type: "image/png",
    };
    const { result } = renderHook(() => useChat("conv_1", []));
    await result.current.send("look", [ref]);

    // setMessages is React-scheduled; waitFor flushes the re-render.
    await waitFor(() => {
      expect(
        result.current.messages.find((m) => m.role === "user"),
      ).toBeDefined();
    });
    const userMsg = result.current.messages.find((m) => m.role === "user");
    expect(userMsg?.content).toBe("look");
    expect(userMsg?.images).toEqual([ref]);
  });

  it("Authorization header still carries Bearer JWT (auth path unchanged)", async () => {
    const { captured, restore: r } = installFetchCapture();
    restore = r;

    const { result } = renderHook(() => useChat("conv_1", []));
    await result.current.send("ping");
    await waitFor(() => expect(captured.length).toBeGreaterThan(0));

    expect(captured[0].headers.Authorization).toBe("Bearer test-jwt-token");
    expect(captured[0].headers["Content-Type"]).toBe("application/json");
  });
});
