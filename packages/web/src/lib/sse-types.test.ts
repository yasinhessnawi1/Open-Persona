import { describe, expect, it } from "vitest";
import {
  type ActivityStartData,
  type ChatDoneData,
  type MemoryRecallData,
  parseChatEvent,
  parseRunEvent,
} from "./sse-types";

/**
 * Spec 31 (D-31-1/2) — the chat `done` frame carries the SEPARATE, additive
 * routing + budget fields, and stays back-compatible when they're absent.
 */
describe("parseChatEvent — done routing + budget (Spec 31)", () => {
  it("carries the routing summary + budget snapshot when present", () => {
    const raw = {
      event: "done",
      data: JSON.stringify({
        usage: { prompt_tokens: 10, completion_tokens: 5 },
        tier: "frontier",
        format_hints: {},
        routing: {
          chosen_model: "anthropic/good",
          dominant_factor: "quality",
          model_fallback_engaged: false,
          model_fallback_reason: null,
        },
        budget: { session_spent_cents: 1.5, max_cents_per_session: 50 },
      }),
    };
    const ev = parseChatEvent(raw);
    expect(ev?.event).toBe("done");
    const data = ev?.data as ChatDoneData;
    expect(data.routing?.chosen_model).toBe("anthropic/good");
    expect(data.routing?.dominant_factor).toBe("quality");
    expect(data.budget?.session_spent_cents).toBe(1.5);
    expect(data.budget?.max_cents_per_session).toBe(50);
  });

  it("a rule-based done frame omits routing + budget (back-compat)", () => {
    const raw = {
      event: "done",
      data: JSON.stringify({
        usage: {},
        tier: "mid",
        format_hints: {},
      }),
    };
    const ev = parseChatEvent(raw);
    const data = ev?.data as ChatDoneData;
    expect(data.tier).toBe("mid");
    expect(data.routing).toBeUndefined();
    expect(data.budget).toBeUndefined();
  });
});

/**
 * Spec 35 (D-35-4) — the chat stream now parses the `memory_recall` frame (it
 * was previously dropped by the CHAT_EVENTS whitelist), naming the typed store.
 */
describe("parseChatEvent — memory_recall (Spec 35)", () => {
  it("parses a memory_recall frame naming the store + count", () => {
    const raw = {
      event: "memory_recall",
      data: JSON.stringify({ store: "episodic", count: 3 }),
    };
    const ev = parseChatEvent(raw);
    expect(ev?.event).toBe("memory_recall");
    const data = ev?.data as MemoryRecallData;
    expect(data.store).toBe("episodic");
    expect(data.count).toBe(3);
  });

  it("count is optional (omitted on the wire)", () => {
    const raw = {
      event: "memory_recall",
      data: JSON.stringify({ store: "identity" }),
    };
    const ev = parseChatEvent(raw);
    const data = ev?.data as MemoryRecallData;
    expect(data.store).toBe("identity");
    expect(data.count).toBeUndefined();
  });
});

describe("activity contract — both SSE transports (P2)", () => {
  const startPayload = {
    activity_id: "a1",
    kind: "web",
    name: "web_search",
    label: "Searching the web",
    args_summary: { q: "rent" },
  };

  it("parses an activity_start chat frame (bare-payload transport)", () => {
    const ev = parseChatEvent({
      event: "activity_start",
      data: JSON.stringify(startPayload),
    });
    expect(ev?.event).toBe("activity_start");
    const data = ev?.data as ActivityStartData;
    expect(data.activity_id).toBe("a1");
    expect(data.kind).toBe("web");
    expect(data.args_summary).toEqual({ q: "rent" });
  });

  it("parses an activity_end chat frame", () => {
    const ev = parseChatEvent({
      event: "activity_end",
      data: JSON.stringify({
        activity_id: "a1",
        status: "ok",
        duration_ms: 9,
        is_error: false,
      }),
    });
    expect(ev?.event).toBe("activity_end");
  });

  it("parses an activity_start run frame (RunEvent envelope transport)", () => {
    const ev = parseRunEvent({
      event: "message",
      data: JSON.stringify({
        type: "activity_start",
        step: 0,
        timestamp: "2026-06-27T00:00:00Z",
        data: startPayload,
      }),
    });
    expect(ev?.type).toBe("activity_start");
    expect(ev?.step).toBe(0);
  });
});
