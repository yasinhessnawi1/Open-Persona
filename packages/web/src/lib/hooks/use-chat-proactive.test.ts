import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChat } from "./use-chat";

/**
 * Spec 30 T05 (D-30-2) — the in-chat proactive-question rail.
 *
 * Verifies the hook surface: an `asking_user` chat SSE frame attaches a
 * `proactive` prompt (with its proposal) to the streaming assistant turn, and
 * accepting the enable option grants the capability (POST /personas/{id}/tools)
 * then RE-SENDS the prior user message (surface-and-retry).
 */

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: () => Promise.resolve("test-jwt-token") }),
}));

function sseResponse(frames: string): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(frames));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

interface Captured {
  url: string;
  method: string;
  body: string;
}

function installFetch(): { captured: Captured[]; restore: () => void } {
  const captured: Captured[] = [];
  const original = globalThis.fetch;
  let messageCalls = 0;
  globalThis.fetch = vi.fn(
    async (url: string | URL | Request, init?: RequestInit) => {
      // openapi-fetch may call fetch(Request) — read method/url/body off the
      // Request when init is absent, so the capture is accurate either way.
      const isRequest = typeof url === "object" && "method" in url;
      const req = isRequest ? (url as Request) : null;
      const urlStr = req ? req.url : url.toString();
      const method = init?.method ?? req?.method ?? "GET";
      let body = typeof init?.body === "string" ? init.body : "";
      if (!body && req) body = await req.clone().text();
      captured.push({ url: urlStr, method, body });
      if (method === "POST" && urlStr.includes("/messages")) {
        messageCalls += 1;
        // First turn: stream a gap-offer that ends with an asking_user rail.
        // Retry turn: empty stream (the grant took effect server-side).
        if (messageCalls === 1) {
          return sseResponse(
            'event: chunk\ndata: {"delta":"I don\'t have that tool. Enable it?","is_final":false}\n\n' +
              'event: asking_user\ndata: {"question":"Enable web_search?",' +
              '"options":[{"label":"Enable it and retry","description":"add it"},' +
              '{"label":"Find another way","description":"x"},' +
              '{"label":"Just explain","description":"y"}],"allow_free_form":true,' +
              '"proposal":{"kind":"tool","name":"web_search","action":"grant_tool","provider":"builtin"}}\n\n' +
              'event: done\ndata: {"usage":{},"tier":"frontier","format_hints":{}}\n\n',
          );
        }
        return sseResponse(
          'event: done\ndata: {"usage":{},"tier":"frontier","format_hints":{}}\n\n',
        );
      }
      // The grant endpoint (POST /personas/{id}/tools) + anything else.
      return new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" },
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

describe("useChat — in-chat proactive rail (spec 30 T05)", () => {
  let restore: () => void;
  afterEach(() => restore?.());

  it("attaches a proactive prompt with its proposal to the assistant turn", async () => {
    const { restore: r } = installFetch();
    restore = r;
    const { result } = renderHook(() => useChat("conv_1", [], "persona_1"));

    await result.current.send("search the web for mould law");
    await waitFor(() => {
      const asst = result.current.messages.find((m) => m.role === "assistant");
      expect(asst?.proactive?.proposal?.name).toBe("web_search");
    });
    const asst = result.current.messages.find((m) => m.role === "assistant");
    expect(asst?.proactive?.question).toBe("Enable web_search?");
    expect(asst?.proactive?.proposal?.action).toBe("grant_tool");
  });

  it("accept → grants the capability then re-sends the prior message", async () => {
    const { captured, restore: r } = installFetch();
    restore = r;
    const { result } = renderHook(() => useChat("conv_1", [], "persona_1"));

    await result.current.send("search the web for mould law");
    await waitFor(() => {
      expect(
        result.current.messages.find((m) => m.role === "assistant")?.proactive,
      ).toBeTruthy();
    });
    const asst = result.current.messages.find((m) => m.role === "assistant");
    if (!asst?.proactive?.proposal) throw new Error("no proposal");

    await result.current.respondToProactive(asst.id, "Enable it and retry", {
      isAccept: true,
      proposal: asst.proactive.proposal,
    });

    await waitFor(() => {
      expect(
        captured.some(
          (c) =>
            c.method === "POST" &&
            c.url.includes("/v1/personas/persona_1/tools"),
        ),
      ).toBe(true);
    });
    const grant = captured.find((c) => c.url.includes("/tools"));
    expect(JSON.parse(grant?.body ?? "{}")).toEqual({
      tool_name: "web_search",
    });
    // A retry message POST followed the grant (surface-and-retry).
    const messagePosts = captured.filter(
      (c) => c.method === "POST" && c.url.includes("/messages"),
    );
    expect(messagePosts.length).toBeGreaterThanOrEqual(2);
  });
});
