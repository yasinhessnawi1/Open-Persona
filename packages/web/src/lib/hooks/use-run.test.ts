import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RunStatusResponse } from "@/lib/api";
import { useRun } from "./use-run";

/**
 * Spec P1 (T8) — run-view reattach VERIFY-AND-HARDEN.
 *
 * Runs already reattach durably (D-P1-runs-no-migration): `runs.steps` is the
 * persisted event-log, the page re-fetches it on return (seed), and `useRun`
 * tails `…/events` + reconciles on stream end. These tests pin that contract
 * (previously untested at the hook level) AND the T3 restart-sweep integration:
 * a run swept to `error` reconciles cleanly to a terminal view — no spin.
 */

// A STABLE getToken (like Clerk's memoized one) so `useRun`'s token/refetch
// callbacks keep identity across renders — otherwise the reattach effect would
// re-fire every render (a test artifact, not prod behavior).
vi.mock("@clerk/nextjs", () => {
  const getToken = () => Promise.resolve("test-jwt-token");
  return { useAuth: () => ({ getToken }) };
});

const TS = "2026-01-01T00:00:00Z";

/** Extract the URL string from fetch's arg — the typed client passes a `Request`. */
function urlOf(u: string | URL | Request): string {
  if (typeof u === "string") return u;
  return u instanceof Request ? u.url : u.toString();
}

function sse(frames: string[]): Response {
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

function blockingSse(): Response {
  return new Response(
    new ReadableStream({
      start(c) {
        c.enqueue(
          new TextEncoder().encode(runFrame("started", -1, { task: "t" })),
        );
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

function runFrame(
  type: string,
  step: number,
  data: Record<string, unknown>,
): string {
  return `event: ${type}\ndata: ${JSON.stringify({ type, step, data, timestamp: TS })}\n\n`;
}

const END = "event: end\ndata: {}\n\n";

function snapshot(over: Partial<RunStatusResponse>): RunStatusResponse {
  return {
    id: "run_1",
    persona_id: "p",
    task: "t",
    status: "running",
    steps: [],
    output: null,
    error: null,
    ...over,
  } as RunStatusResponse;
}

interface RunMock {
  eventsResponse: () => Response;
  reconcile: RunStatusResponse;
  onEventsSignal?: (s: AbortSignal | null | undefined) => void;
  eventsHit?: { count: number };
}

function installRunFetch(mock: RunMock): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = vi.fn(
    async (url: string | URL | Request, init?: RequestInit) => {
      const u = urlOf(url);
      if (u.includes("/runs/") && u.endsWith("/events")) {
        if (mock.eventsHit) mock.eventsHit.count += 1;
        mock.onEventsSignal?.(init?.signal);
        return mock.eventsResponse();
      }
      // GET /v1/runs/{id} — the reconcile snapshot (the typed client → a Request).
      if (/\/v1\/runs\/[^/]+$/.test(u)) return json(mock.reconcile);
      return json({});
    },
  ) as unknown as typeof fetch;
  return () => {
    globalThis.fetch = original;
  };
}

describe("useRun — Spec P1 reattach verify-and-harden", () => {
  let restore: () => void;
  afterEach(() => restore?.());

  it("a terminal run at load opens NO event stream", async () => {
    const hit = { count: 0 };
    restore = installRunFetch({
      eventsResponse: () => sse([END]),
      reconcile: snapshot({ status: "completed" }),
      eventsHit: hit,
    });
    const initial = snapshot({ status: "completed", output: "done" });
    const { result } = renderHook(() => useRun("run_1", initial));
    // Settle a tick; the effect must early-return for a terminal run.
    await new Promise((r) => setTimeout(r, 20));
    expect(hit.count).toBe(0);
    expect(result.current.view.status).toBe("completed");
  });

  it("seeds gap-arrived steps from the snapshot on mount (reattach-on-return)", () => {
    restore = installRunFetch({
      eventsResponse: () => sse([END]),
      reconcile: snapshot({ status: "running" }),
    });
    // The page re-fetch on return carries steps that occurred WHILE AWAY (the
    // persisted event-log). They must render immediately from the seed.
    const initial = snapshot({
      status: "running",
      steps: [
        { type: "started", step: -1, data: { task: "t" }, timestamp: TS },
        {
          type: "tool_calling",
          step: 0,
          data: {
            tool_calls: [{ name: "web_search", args: {}, call_id: "c1" }],
          },
          timestamp: TS,
        },
      ] as unknown as RunStatusResponse["steps"],
    });
    const { result } = renderHook(() => useRun("run_1", initial));
    // The gap-arrived step is in the view from the very first render.
    expect(result.current.view.steps.length).toBeGreaterThanOrEqual(1);
  });

  it("tails the live stream then reconciles to the terminal snapshot on end", async () => {
    restore = installRunFetch({
      eventsResponse: () =>
        sse([runFrame("completed", 0, { output: "done" }), END]),
      reconcile: snapshot({ status: "completed", output: "done" }),
    });
    const initial = snapshot({ status: "running" });
    const { result } = renderHook(() => useRun("run_1", initial));
    await waitFor(() => expect(result.current.view.status).toBe("completed"));
  });

  it("HARDEN: a restart-swept run (events 404 → reconcile to 'error') settles, no spin", async () => {
    const hit = { count: 0 };
    restore = installRunFetch({
      // The handle is gone after a restart → the live stream 404s.
      eventsResponse: () => json({ error: "run_not_found" }, 404),
      // The startup sweep (T3) reconciled the orphaned run to `error`.
      reconcile: snapshot({
        status: "error",
        error: "interrupted by a server restart",
      }),
      eventsHit: hit,
    });
    const initial = snapshot({ status: "running" });
    const { result } = renderHook(() => useRun("run_1", initial));
    await waitFor(() => expect(result.current.view.status).toBe("error"));
    expect(result.current.view.error).toContain("restart");
    // The dead run reconciles to a terminal state and STOPS — it does NOT spin
    // forever. The load-bearing proof is STABILITY: after settling, no further
    // event-stream attempts are made (a spin would keep climbing). A generous
    // sanity bound guards against a runaway even within the settle window.
    await new Promise((r) => setTimeout(r, 150)); // let every in-flight drive settle
    const settled = hit.count;
    expect(settled).toBeLessThan(16);
    await new Promise((r) => setTimeout(r, 80));
    expect(hit.count).toBe(settled); // stable — no further attempts (no spin)
  });

  it("aborts the live event stream on unmount", async () => {
    let captured: AbortSignal | null | undefined;
    restore = installRunFetch({
      eventsResponse: () => blockingSse(),
      reconcile: snapshot({ status: "running" }),
      onEventsSignal: (s) => {
        captured = s;
      },
    });
    const initial = snapshot({ status: "running" });
    const { unmount } = renderHook(() => useRun("run_1", initial));
    await waitFor(() => expect(captured).toBeTruthy());
    expect(captured?.aborted).toBe(false);
    unmount();
    expect(captured?.aborted).toBe(true);
  });
});
