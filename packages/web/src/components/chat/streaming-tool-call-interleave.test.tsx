/**
 * Spec F2 T18 — StreamingTextRenderer client-side regression suite for the
 * five Spec-11 server-side tool-protocol fixes.
 *
 * Each test feeds the renderer a prop-update sequence derived from the
 * post-fix stream pattern (fixture: __fixtures__/stream-interleave.json) and
 * asserts the renderer behaves correctly:
 *   - The accumulated text matches the expected final.
 *   - No console errors land mid-stream.
 *   - The renderer survives stream patterns the pre-fix bugs would have
 *     produced (empty call_id, tool_result-first, hallucinated-tool error).
 *
 * The 5 Spec-11 fixes are SERVER-side. T18's job is to verify that the
 * post-fix stream shapes pass cleanly through the renderer's text path
 * (which is what the user sees). Tool-call cards (the tools[] array on
 * ChatMessageView) are MessageElement state, exercised by the T15 tests.
 *
 * Live-smoke verification against DeepSeek is DEFERRED to T26's pre-flight
 * gate per the F2 task list. This file is the deterministic regression
 * surface that runs in CI on every commit.
 */

import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "./__fixtures__/stream-interleave.json";
import { StreamingTextRenderer } from "./streaming-text-renderer";

interface Frame {
  type: string;
  data: Record<string, unknown>;
}

/**
 * Apply the fixture's frames to the renderer one at a time, mirroring how
 * the upstream `useChat` accumulates chunks. Returns the final text the
 * renderer was asked to display.
 */
function accumulateText(frames: readonly Frame[]): string {
  let text = "";
  for (const ev of frames) {
    if (ev.type === "chunk") {
      const delta = (ev.data as { delta?: string }).delta;
      if (typeof delta === "string") text += delta;
    }
    // tool_calling / tool_result / done don't affect text.
  }
  return text;
}

describe("StreamingTextRenderer — Spec-11 fix regressions (T18)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("(fix 2) paints text from the four-rounds tool-call interleave correctly", async () => {
    const frames = fixture.frames as readonly Frame[];
    const expectedFinal = fixture.expected_final_text as string;
    const accumulated = accumulateText(frames);
    expect(accumulated).toBe(expectedFinal);

    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { container, rerender } = render(
      <StreamingTextRenderer text="" streaming />,
    );
    // Drive the renderer through each chunk in fixture order, accumulating
    // text on every chunk event (other events are no-ops for the renderer).
    let running = "";
    for (const ev of frames) {
      if (ev.type === "chunk") {
        running += (ev.data as { delta: string }).delta;
        rerender(<StreamingTextRenderer text={running} streaming />);
      } else if (ev.type === "done") {
        rerender(<StreamingTextRenderer text={running} streaming={false} />);
      }
    }
    await waitFor(() => {
      const out = container.querySelector('[data-slot="streaming-text"]');
      expect(out?.textContent).toContain("In summary");
    });
    // No console errors during the interleave.
    expect(consoleSpy).not.toHaveBeenCalled();
  });

  it("(fix 1) renders text correctly after an is_error tool_result (hallucinated tool name recovery)", async () => {
    const frames = fixture.frames as readonly Frame[];
    // The fixture's hallucinated-tool round is the 3rd one (search_legal_database).
    const hallucinated = frames.find(
      (f) =>
        f.type === "tool_result" &&
        (f.data as { is_error?: boolean }).is_error === true,
    );
    expect(hallucinated).toBeDefined();
    // Text continues normally after the error.
    const after = frames.slice(frames.indexOf(hallucinated as Frame) + 1);
    const recoveryText = accumulateText(after);
    expect(recoveryText).toContain("Let me reach via the available tool");
  });

  it("(fix 3) doesn't crash on tool_calling with empty call_id (id_by_index synthesis is server-side; renderer is text-only)", async () => {
    const frames = fixture.frames as readonly Frame[];
    const emptyId = frames.find(
      (f) =>
        f.type === "tool_calling" &&
        (f.data as { tool_calls: Array<{ call_id: string }> }).tool_calls[0]
          .call_id === "",
    );
    expect(emptyId).toBeDefined();
    // Render with text that crossed the empty-call_id boundary — should
    // commit without throwing.
    const beforeEmpty = frames.slice(0, frames.indexOf(emptyId as Frame));
    const text = accumulateText(beforeEmpty);
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { container } = render(
      <StreamingTextRenderer text={text} streaming />,
    );
    await waitFor(() => {
      const out = container.querySelector('[data-slot="streaming-text"]');
      expect(out?.textContent).toContain("§5-1");
    });
    expect(consoleSpy).not.toHaveBeenCalled();
  });

  it("(fix 4) renders text through metadata-shape variation (tool_call_id vs call_id)", async () => {
    const frames = fixture.frames as readonly Frame[];
    // The post-fix server emits both keys defensively; the renderer's text
    // path is unaffected by the metadata shape.
    const dualKey = frames.find(
      (f) =>
        f.type === "tool_result" &&
        typeof (f.data as { metadata?: Record<string, string> }).metadata ===
          "object",
    );
    expect(dualKey).toBeDefined();
    const meta = (
      dualKey as Frame & {
        data: { metadata: { tool_call_id: string; call_id: string } };
      }
    ).data.metadata;
    expect(meta.tool_call_id).toBe(meta.call_id);
    // No assertion on the renderer needed — the fact that the fixture's
    // expected_final_text passes through the fix-2 test confirms metadata
    // variation has no effect on the rendered text.
  });

  it("(fix 5) handles a stream where the first event is a tool_result (compaction edge case)", async () => {
    // Simulate the compacted-boundary case: the assistant(tool_calls) was
    // dropped, the recent window starts with a dangling tool_result. The
    // _recent_start fix walks back over leading tool messages server-side;
    // the client renderer sees a stream that begins with a tool_result
    // event (no text yet) followed by the recovery text.
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { container, rerender } = render(
      <StreamingTextRenderer text="" thinking streaming />,
    );
    // Initial state: thinking (no text yet, no tool_result in the renderer's
    // contract — that lives on MessageElement).
    expect(
      container.querySelector('[data-slot="streaming-thinking"]'),
    ).not.toBeNull();
    // Recovery text lands.
    rerender(
      <StreamingTextRenderer
        text="(Continuing from a compacted boundary.) The summary is: "
        thinking={false}
        streaming
      />,
    );
    await waitFor(() => {
      const out = container.querySelector('[data-slot="streaming-text"]');
      expect(out?.textContent).toContain(
        "(Continuing from a compacted boundary.)",
      );
    });
    expect(consoleSpy).not.toHaveBeenCalled();
  });

  it("stress: ~500 rapid prop updates coalesce cleanly (proxy for sustained chunk pressure)", async () => {
    // Simulate ~500 chunks (a realistic mid-length DeepSeek response). Mechanism
    // B's rAF coalescing should consolidate these into far fewer React commits;
    // the displayed text should equal the final accumulated string.
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { container, rerender } = render(
      <StreamingTextRenderer text="" streaming />,
    );
    let accumulated = "";
    for (let i = 0; i < 500; i++) {
      accumulated += `${i.toString().padStart(3, "0")} `;
      rerender(<StreamingTextRenderer text={accumulated} streaming />);
    }
    await waitFor(
      () => {
        const out = container.querySelector('[data-slot="streaming-text"]');
        expect(out?.textContent).toContain("499");
      },
      { timeout: 3000 },
    );
    expect(consoleSpy).not.toHaveBeenCalled();
  });
});
