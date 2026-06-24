import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChat } from "./use-chat";

/**
 * Spec P1 (T7) — `useChat` reattach-on-mount.
 *
 * On return to a conversation with a live turn, the hook detects it via
 * `GET …/active-turn`, marks the assistant turn streaming, resubscribes to the
 * live tail (`…/active-turn/events`) with the SAME frame handling as `send`, and
 * reconciles via persisted history on stream end so the final content is
 * authoritative. A 404 from `…/active-turn` means no live turn — no reattach.
 */

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: () => Promise.resolve("test-jwt-token") }),
}));

function sse(frames: string[]): Response {
  const body = frames.join("");
  return new Response(
    new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode(body));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

function json(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function chunkFrame(delta: string): string {
  return `event: chunk\ndata: ${JSON.stringify({ delta, is_final: false })}\n\n`;
}

const DONE = `event: done\ndata: ${JSON.stringify({ tier: "mid", usage: {}, format_hints: {} })}\n\n`;

/** A live-tail stream that emits one chunk and never closes (still streaming). */
function blockingSse(): Response {
  return new Response(
    new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode(chunkFrame("partial ")));
        // Never closed — the tail stays open so the controller is live at unmount.
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

interface MockOpts {
  active404?: boolean;
  finalContent?: string;
  blockEvents?: boolean;
  onEventsSignal?: (s: AbortSignal | null | undefined) => void;
}

function installRoutedFetch(opts: MockOpts): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = vi.fn(
    async (url: string | URL | Request, init?: RequestInit) => {
      const u = url.toString();
      if (u.endsWith("/active-turn")) {
        return opts.active404
          ? json({ error: "turn_not_active" }, 404)
          : json({
              message_id: "m1",
              streaming_status: "running",
              content: "Hello",
              stream_events: [],
            });
      }
      if (u.includes("/active-turn/events")) {
        opts.onEventsSignal?.(init?.signal);
        return opts.blockEvents
          ? blockingSse()
          : sse([chunkFrame(" world"), DONE]);
      }
      // GET /v1/conversations/{id} — the reconcile (and reload) path.
      if (/\/v1\/conversations\/[^/]+$/.test(u)) {
        return json({
          id: "conv_1",
          persona_id: "p",
          title: "t",
          messages: [
            { id: "u1", role: "user", content: "hi" },
            {
              id: "m1",
              role: "assistant",
              content: opts.finalContent ?? "Hello world",
            },
          ],
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        });
      }
      return sse([]);
    },
  ) as unknown as typeof fetch;
  return () => {
    globalThis.fetch = original;
  };
}

describe("useChat — Spec P1 reattach-on-mount", () => {
  let restore: () => void;
  afterEach(() => restore?.());

  it("reattaches to a live turn, tails it, and reconciles to the authoritative final", async () => {
    restore = installRoutedFetch({ finalContent: "Hello world" });
    const initial = [
      { id: "u1" as string, role: "user" as const, content: "hi" },
      { id: "m1" as string, role: "assistant" as const, content: "Hello" },
    ];
    const { result } = renderHook(() => useChat("conv_1", initial, "p"));

    await waitFor(() => {
      const a = result.current.messages.find((m) => m.id === "m1");
      expect(a?.content).toBe("Hello world");
    });
    // Stream ended → not streaming; the reconcile replaced the seed with the
    // persisted final (no gap, no double).
    expect(result.current.streaming).toBe(false);
  });

  it("does NOT reattach when there is no active turn (404)", async () => {
    restore = installRoutedFetch({ active404: true });
    const { result } = renderHook(() => useChat("conv_1", [], "p"));

    // Give the mount effect a tick; it must settle to not-streaming with no turn.
    await waitFor(() => expect(result.current.streaming).toBe(false));
    expect(result.current.messages).toEqual([]);
  });

  it("aborts the live-tail fetch on unmount (the detached turn keeps running)", async () => {
    let captured: AbortSignal | null | undefined;
    restore = installRoutedFetch({
      blockEvents: true,
      onEventsSignal: (s) => {
        captured = s;
      },
    });
    const { unmount } = renderHook(() =>
      useChat(
        "conv_1",
        [{ id: "m1" as string, role: "assistant" as const, content: "Hello" }],
        "p",
      ),
    );
    await waitFor(() => expect(captured).toBeTruthy());
    expect(captured?.aborted).toBe(false);
    unmount();
    expect(captured?.aborted).toBe(true);
  });
});
