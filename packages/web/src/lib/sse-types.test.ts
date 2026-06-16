import { describe, expect, it } from "vitest";
import { type ChatDoneData, parseChatEvent } from "./sse-types";

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
