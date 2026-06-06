/**
 * Spec F2 T17 — StreamingTextRenderer tests.
 *
 * Verifies the mechanism B contract (useTransition + rAF-coalesced append)
 * + the D-F2-12 vermilion caret + the D-F2-13 thinking indicator + edge
 * cases (shrink, reset, no-op).
 *
 * jsdom provides requestAnimationFrame; vi.useFakeTimers + advanceTimers
 * drives the rAF flushes deterministically in tests.
 */

import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StreamingTextRenderer } from "./streaming-text-renderer";

describe("StreamingTextRenderer", () => {
  beforeEach(() => {
    // Real timers + rAF: simpler than mocking; jsdom supports them. Use
    // waitFor for assertions that depend on the rAF flush landing.
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the initial text synchronously", () => {
    const { container } = render(
      <StreamingTextRenderer text="Hello" streaming={false} />,
    );
    const out = container.querySelector('[data-slot="streaming-text"]');
    expect(out?.textContent).toBe("Hello");
  });

  it("shows the vermilion caret when streaming=true (D-F2-12 provisional)", () => {
    const { container } = render(
      <StreamingTextRenderer text="Astrid" streaming />,
    );
    const caret = container.querySelector('[data-slot="streaming-caret"]');
    expect(caret).not.toBeNull();
    // Vermilion --primary class.
    expect(caret?.className).toContain("bg-primary");
    // Decorative — aria-hidden.
    expect(caret?.getAttribute("aria-hidden")).toBe("true");
  });

  it("hides the caret when streaming=false", () => {
    const { container } = render(
      <StreamingTextRenderer text="Done." streaming={false} />,
    );
    expect(container.querySelector('[data-slot="streaming-caret"]')).toBeNull();
  });

  it("renders ThinkingIndicator when thinking=true && text is empty (D-F2-13)", () => {
    const { container } = render(
      <StreamingTextRenderer text="" thinking streaming={false} />,
    );
    const thinking = container.querySelector(
      '[data-slot="streaming-thinking"]',
    );
    expect(thinking).not.toBeNull();
    // <output> has implicit ARIA role="status" — preferred over explicit role
    // attribute per Biome useSemanticElements.
    expect(thinking?.tagName).toBe("OUTPUT");
    expect(thinking?.getAttribute("aria-label")).toBe("Thinking");
    // No streaming-text wrapper while thinking.
    expect(container.querySelector('[data-slot="streaming-text"]')).toBeNull();
  });

  it("(F2 T25) accepts a custom thinkingLabel for i18n delegation", () => {
    const { container } = render(
      <StreamingTextRenderer
        text=""
        thinking
        streaming={false}
        thinkingLabel="Astrid is thinking…"
      />,
    );
    const thinking = container.querySelector(
      '[data-slot="streaming-thinking"]',
    );
    expect(thinking?.getAttribute("aria-label")).toBe("Astrid is thinking…");
  });

  it("switches from ThinkingIndicator to streaming text when the first chunk lands", async () => {
    const { container, rerender } = render(
      <StreamingTextRenderer text="" thinking streaming />,
    );
    expect(
      container.querySelector('[data-slot="streaming-thinking"]'),
    ).not.toBeNull();

    rerender(<StreamingTextRenderer text="The " thinking={false} streaming />);
    // rAF + useTransition: wait for the displayed text to land.
    await waitFor(() => {
      const out = container.querySelector('[data-slot="streaming-text"]');
      expect(out?.textContent).toContain("The");
    });
    // Caret now visible.
    expect(
      container.querySelector('[data-slot="streaming-caret"]'),
    ).not.toBeNull();
  });

  it("coalesces multiple prop updates into the displayed text via rAF + transition", async () => {
    const { container, rerender } = render(
      <StreamingTextRenderer text="" streaming />,
    );
    // Simulate three rapid prop updates (chunks arriving).
    rerender(<StreamingTextRenderer text="Hel" streaming />);
    rerender(<StreamingTextRenderer text="Hello" streaming />);
    rerender(<StreamingTextRenderer text="Hello, " streaming />);
    rerender(<StreamingTextRenderer text="Hello, world." streaming />);
    await waitFor(() => {
      const out = container.querySelector('[data-slot="streaming-text"]');
      expect(out?.textContent).toContain("Hello, world.");
    });
  });

  it("handles a text reset (shrink) synchronously (e.g., conversation switch)", async () => {
    const { container, rerender } = render(
      <StreamingTextRenderer text="A long streamed response..." streaming />,
    );
    await waitFor(() => {
      const out = container.querySelector('[data-slot="streaming-text"]');
      expect(out?.textContent).toContain("A long streamed response");
    });
    // Reset to empty (conversation switched).
    rerender(<StreamingTextRenderer text="" streaming={false} />);
    // Sync commit — should be empty immediately on the next render.
    await waitFor(() => {
      const out = container.querySelector('[data-slot="streaming-text"]');
      expect(out?.textContent).toBe("");
    });
  });

  it("commits non-streaming terminal text synchronously (no transition)", () => {
    const { container, rerender } = render(
      <StreamingTextRenderer text="" streaming={false} />,
    );
    // Terminal text (e.g., a non-streamed assistant message loaded from
    // history) lands without rAF coalescing.
    act(() => {
      rerender(
        <StreamingTextRenderer text="Loaded from history." streaming={false} />,
      );
    });
    const out = container.querySelector('[data-slot="streaming-text"]');
    expect(out?.textContent).toBe("Loaded from history.");
    expect(container.querySelector('[data-slot="streaming-caret"]')).toBeNull();
  });

  it("exposes data-streaming on the output for downstream querying", () => {
    const { container: streamingC } = render(
      <StreamingTextRenderer text="x" streaming />,
    );
    expect(
      streamingC
        .querySelector('[data-slot="streaming-text"]')
        ?.getAttribute("data-streaming"),
    ).toBe("true");
    const { container: doneC } = render(
      <StreamingTextRenderer text="x" streaming={false} />,
    );
    expect(
      doneC
        .querySelector('[data-slot="streaming-text"]')
        ?.getAttribute("data-streaming"),
    ).toBe("false");
  });

  it("uses aria-live=polite on the output (screen readers announce streamed text)", () => {
    const { container } = render(
      <StreamingTextRenderer text="Astrid replies" streaming />,
    );
    const out = container.querySelector('[data-slot="streaming-text"]');
    expect(out?.getAttribute("aria-live")).toBe("polite");
  });
});
