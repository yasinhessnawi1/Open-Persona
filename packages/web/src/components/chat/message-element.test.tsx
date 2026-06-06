/**
 * Spec F2 T15 — MessageElement tests.
 *
 * Verifies:
 *   1. User vs persona branches render distinctly.
 *   2. D-F1-5 composite on persona path: 2px identity-coloured border-left,
 *      neutral bg-card surface.
 *   3. D-F2-7 once-per-turn rule: avatar renders on first persona message of
 *      a turn; hidden on consecutive persona messages; re-renders after a
 *      user message breaks the turn.
 *   4. Tool-call cards stack above the text content when present.
 *   5. Streaming uses T17 StreamingTextRenderer (delegates correctly).
 *   6. Terminal text uses T11 Markdown (when content is present).
 *   7. TierBadge renders when terminal + tier set; hidden during streaming.
 */

import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import { derivePersonaIdentityColor } from "@/lib/persona-identity";
import {
  MessageElement,
  type MessageElementView,
  type MessageEvent,
} from "./message-element";

const ASTRID = {
  id: "astrid_tenancy_law",
  name: "Astrid",
} as const;

const messages = {
  chat: {
    tierLabel: "{tier} tier",
    toolUsing: "Using {tool}",
    toolError: "error",
    thinking: "{name} is thinking…",
    toolRunning: "{name} is using {tool}…",
  },
};

function renderWithIntl(node: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      {node}
    </NextIntlClientProvider>,
  );
}

const userMsg = (content: string): MessageElementView => ({
  id: `u-${content}`,
  role: "user",
  content,
});

const personaMsg = (
  content: string,
  extra: Partial<MessageElementView> = {},
): MessageElementView => ({
  id: `a-${content}`,
  role: "assistant",
  content,
  ...extra,
});

describe("MessageElement", () => {
  it("renders user messages right-aligned with bg-secondary", () => {
    const { container } = renderWithIntl(
      <MessageElement message={userMsg("Hello Astrid")} persona={ASTRID} />,
    );
    const wrap = container.querySelector('[data-slot="message-element"]');
    expect(wrap).not.toBeNull();
    expect(wrap?.getAttribute("data-role")).toBe("user");
    expect(wrap?.className).toContain("justify-end");
    const bubble = wrap?.firstElementChild as HTMLElement | null;
    expect(bubble?.className).toContain("bg-secondary");
    expect(bubble?.textContent).toBe("Hello Astrid");
  });

  it("renders persona messages with the D-F1-5 composite — 2px identity-coloured border-left + bg-card", () => {
    const { container } = renderWithIntl(
      <MessageElement message={personaMsg("Good morning.")} persona={ASTRID} />,
    );
    const wrap = container.querySelector('[data-slot="message-element"]');
    expect(wrap?.getAttribute("data-role")).toBe("persona");
    const body = container.querySelector(
      '[data-slot="message-element-body"]',
    ) as HTMLElement | null;
    expect(body).not.toBeNull();
    // 2px border-left + bg-card.
    expect(body?.className).toContain("border-l-2");
    expect(body?.className).toContain("bg-card");
    // Identity-coloured.
    const expected = derivePersonaIdentityColor(ASTRID).oklch;
    expect(body?.style.borderLeftColor).toBe(expected);
  });

  it("(D-F2-7) renders avatar on first persona message (no prevMessage)", () => {
    const { container } = renderWithIntl(
      <MessageElement message={personaMsg("Astrid here.")} persona={ASTRID} />,
    );
    const wrap = container.querySelector('[data-slot="message-element"]');
    expect(wrap?.getAttribute("data-shows-avatar")).toBe("true");
    expect(container.querySelector('[role="img"]')).not.toBeNull();
  });

  it("(D-F2-7) hides avatar on consecutive persona message (same-turn continuation)", () => {
    const first = personaMsg("First reply.");
    const second = personaMsg("Continued reply.");
    const { container } = renderWithIntl(
      <MessageElement message={second} persona={ASTRID} prevMessage={first} />,
    );
    const wrap = container.querySelector('[data-slot="message-element"]');
    expect(wrap?.getAttribute("data-shows-avatar")).toBe("false");
    // The fixed-width avatar slot still occupies space (alignment preserved).
    expect(container.querySelector('[role="img"]')).toBeNull();
  });

  it("(D-F2-7) re-renders avatar after a user message breaks the turn", () => {
    const userBreak = userMsg("Quick question.");
    const personaAfter = personaMsg("Sure — here's my answer.");
    const { container } = renderWithIntl(
      <MessageElement
        message={personaAfter}
        persona={ASTRID}
        prevMessage={userBreak}
      />,
    );
    const wrap = container.querySelector('[data-slot="message-element"]');
    expect(wrap?.getAttribute("data-shows-avatar")).toBe("true");
    expect(container.querySelector('[role="img"]')).not.toBeNull();
  });

  it("stacks tool-call cards above the text content", () => {
    const msg = personaMsg("Found it.", {
      tools: [
        {
          toolName: "web_search",
          args: { q: "husleieloven §5-7" },
          result: "Found.",
          pending: false,
        },
      ],
    });
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    const body = container.querySelector('[data-slot="message-element-body"]');
    // Tool-call card is rendered as a child of the body — verify presence.
    expect(body?.textContent).toContain("web_search");
  });

  it("delegates to StreamingTextRenderer when message.streaming=true", () => {
    const msg = personaMsg("Astrid is thi", { streaming: true });
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    // Streaming renderer mounts → its data-slot is present.
    expect(
      container.querySelector('[data-slot="streaming-text"]'),
    ).not.toBeNull();
    // The Markdown content slot is NOT mounted during streaming.
    expect(
      container.querySelector('[data-slot="message-element-content"]'),
    ).toBeNull();
  });

  it("uses Markdown for terminal text (non-streaming, content present)", () => {
    const msg = personaMsg("**Done.** Here is the answer.");
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    const contentSlot = container.querySelector(
      '[data-slot="message-element-content"]',
    );
    expect(contentSlot).not.toBeNull();
    // Markdown renders **Done.** as <strong>.
    expect(contentSlot?.querySelector("strong")?.textContent).toBe("Done.");
  });

  it("shows TierBadge when terminal + tier set", () => {
    const msg = personaMsg("Final.", { tier: "frontier" });
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    expect(container.querySelector('[data-slot="tier-badge"]')).not.toBeNull();
  });

  it("hides TierBadge during streaming even when tier is set", () => {
    const msg = personaMsg("Streaming...", {
      streaming: true,
      tier: "frontier",
    });
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    expect(container.querySelector('[data-slot="tier-badge"]')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// D-F2-15 interleaved layout (T26 amendment, 2026-06-06)

const interleavedMsg = (
  events: MessageEvent[],
  extra: Partial<MessageElementView> = {},
): MessageElementView => ({
  id: `i-${events.length}`,
  role: "assistant",
  content: events
    .filter(
      (e): e is Extract<MessageEvent, { kind: "text" }> => e.kind === "text",
    )
    .map((e) => e.delta)
    .join(""),
  events,
  ...extra,
});

describe("MessageElement — D-F2-15 interleaved layout", () => {
  it("flips data-layout to 'interleaved' when events[] is present", () => {
    const msg = interleavedMsg([{ kind: "text", delta: "Hi." }]);
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    const wrap = container.querySelector('[data-slot="message-element"]');
    expect(wrap?.getAttribute("data-layout")).toBe("interleaved");
  });

  it("renders text + tool cards in stream order (not stacked)", () => {
    const msg = interleavedMsg([
      { kind: "text", delta: "Let me search. " },
      {
        kind: "tool_call",
        callId: "c0",
        toolName: "web_search",
        args: { q: "husleieloven" },
      },
      {
        kind: "tool_result",
        toolName: "web_search",
        content: "Section 5-1 explains it.",
        isError: false,
      },
      { kind: "text", delta: "Got it." },
    ]);
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    const interleaved = container.querySelector(
      '[data-slot="message-element-interleaved"]',
    );
    expect(interleaved).not.toBeNull();
    const children = Array.from(interleaved?.children ?? []);
    // text span → tool card → text span (3 items, in stream order).
    expect(children.length).toBe(3);
    expect(children[0]?.getAttribute("data-slot")).toBe("message-event-text");
    expect(children[0]?.textContent).toContain("Let me search");
    expect(children[2]?.getAttribute("data-slot")).toBe("message-event-text");
    expect(children[2]?.textContent).toContain("Got it");
  });

  it("pairs tool_call with the next matching tool_result (FIFO by toolName)", () => {
    // ToolCallCard's body is in a <Collapsible>, closed by default, so result
    // content isn't in the DOM until expanded. Instead: verify both cards
    // render, the first marks success (no error indicator), and the second
    // marks error (· error appended from the tool_result's isError flag).
    const msg = interleavedMsg([
      {
        kind: "tool_call",
        callId: "c0",
        toolName: "web_search",
        args: { q: "a" },
      },
      {
        kind: "tool_result",
        toolName: "web_search",
        content: "Result A",
        isError: false,
      },
      {
        kind: "tool_call",
        callId: "c1",
        toolName: "web_search",
        args: { q: "b" },
      },
      {
        kind: "tool_result",
        toolName: "web_search",
        content: "Result B",
        isError: true,
      },
    ]);
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    const interleaved = container.querySelector(
      '[data-slot="message-element-interleaved"]',
    );
    // Count occurrences of the "Using web_search" trigger text — one per
    // tool card. Result content lives inside <Collapsible>, closed by
    // default, so it's not in the rendered textContent until expanded.
    const usingMatches = (interleaved?.textContent ?? "").match(
      /Using web_search/g,
    );
    expect(usingMatches?.length).toBe(2);
    // The second card's tool_result was an error → "· error" appears once.
    const errorMatches = (interleaved?.textContent ?? "").match(/· error/g);
    expect(errorMatches?.length).toBe(1);
  });

  it("shows the thinking indicator when streaming and events is empty", () => {
    const msg = interleavedMsg([], { streaming: true });
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    expect(
      container.querySelector('[data-slot="streaming-thinking"]'),
    ).not.toBeNull();
  });

  it("shows the tool-running indicator when streaming and a tool_call has no result", () => {
    const msg = interleavedMsg(
      [
        { kind: "text", delta: "Searching… " },
        {
          kind: "tool_call",
          callId: "c0",
          toolName: "web_search",
          args: { q: "x" },
        },
      ],
      { streaming: true },
    );
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    const running = container.querySelector(
      '[data-slot="message-element-tool-running"]',
    );
    expect(running).not.toBeNull();
    expect(running?.getAttribute("aria-label")).toContain("Astrid");
    expect(running?.getAttribute("aria-label")).toContain("web_search");
  });

  it("hides the tool-running indicator once a matching tool_result arrives", () => {
    const msg = interleavedMsg(
      [
        {
          kind: "tool_call",
          callId: "c0",
          toolName: "web_search",
          args: { q: "x" },
        },
        {
          kind: "tool_result",
          toolName: "web_search",
          content: "ok",
          isError: false,
        },
      ],
      { streaming: true },
    );
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    expect(
      container.querySelector('[data-slot="message-element-tool-running"]'),
    ).toBeNull();
  });

  it("shows the caret next to the last text span when streaming and last event was text", () => {
    const msg = interleavedMsg(
      [
        {
          kind: "tool_call",
          callId: "c0",
          toolName: "web_search",
          args: { q: "x" },
        },
        {
          kind: "tool_result",
          toolName: "web_search",
          content: "ok",
          isError: false,
        },
        { kind: "text", delta: "I found it." },
      ],
      { streaming: true },
    );
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    expect(
      container.querySelector('[data-slot="message-element-caret"]'),
    ).not.toBeNull();
  });

  it("hides the caret when streaming has ended", () => {
    const msg = interleavedMsg([{ kind: "text", delta: "Done." }], {
      streaming: false,
    });
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    expect(
      container.querySelector('[data-slot="message-element-caret"]'),
    ).toBeNull();
  });

  it("falls back to the stacked layout when events[] is absent (back-compat)", () => {
    const msg = personaMsg("No events here.");
    const { container } = renderWithIntl(
      <MessageElement message={msg} persona={ASTRID} />,
    );
    const wrap = container.querySelector('[data-slot="message-element"]');
    expect(wrap?.getAttribute("data-layout")).toBe("stacked");
  });
});
