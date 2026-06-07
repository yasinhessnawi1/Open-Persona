/**
 * Spec F4 T14 — cross-cutting structural invariants.
 *
 * Beyond per-task tests. Pins guarantees that span the F4 surface and
 * MUST hold regardless of any individual component's evolution:
 *
 *   1. Dispatcher exhaustiveness — every OutputContent variant has a
 *      branch. Failing variant would trip TypeScript at compile time,
 *      but this is the runtime backstop.
 *   2. 1 MB-stays-by-reference (F3 T22 mirror) — produced_files bytes
 *      NEVER inline into OutputContent / wire shapes. Mirror of F3's
 *      "image bytes stay by reference" guarantee for OUTPUT-side.
 *   3. Single-renderer-set — the chat normaliser (T03) and run normaliser
 *      (T04) emit IDENTICAL OutputContent for the same produced_file
 *      payload; downstream renderers see no transport leakage.
 *   4. Path-traversal — the dispatcher swaps to <FailureCard> on `..`
 *      segments before any byte fetch is initiated (defence-in-depth
 *      layer-2; backend resolver is the primary).
 *   5. Cross-surface consistency (criterion 6) — rendering the SAME
 *      OutputContent through `<OutputDispatcher>` produces the same
 *      data-slot DOM regardless of caller.
 *   6. Renderer dispatch table parity with the path-IS-hint contract —
 *      every R-F4-1 row's path × media-type combination routes to the
 *      expected renderer.
 *
 * If any of these regress, the F4 design contract is violated and the
 * fix-or-document discipline applies.
 */

import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import en from "@/i18n/messages/en.json";
import type { OutputContent } from "@/lib/api/output-content";
import { outputContentSchema } from "@/lib/api/output-content";
import { chatSseToOutputContent } from "@/lib/normalisers/chat-output";
import { runEventToOutputContent } from "@/lib/normalisers/run-output";
import type { ChatEvent, RunEvent } from "@/lib/sse-types";

import { OutputDispatcher } from "../dispatcher";

vi.mock("@/lib/hooks/use-authed-image-blob-url", () => ({
  useAuthedImageBlobUrl: () => ({
    src: "blob:fake",
    loading: false,
    error: null,
  }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: vi.fn().mockResolvedValue("fake-jwt") }),
}));

vi.mock("../highlighted-code", () => ({
  default: ({ code }: { code: string }) => <pre>{code}</pre>,
}));

function renderWithIntl(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  );
}

const VARIANT_PROBES: Record<OutputContent["kind"], OutputContent> = {
  "inline-image": {
    kind: "inline-image",
    workspace_path: "uploads/abc.png",
    media_type: "image/png",
    alt: "abc.png",
  },
  "inline-chart": {
    kind: "inline-chart",
    workspace_path: "charts/q1.png",
    media_type: "image/png",
  },
  "download-doc": {
    kind: "download-doc",
    workspace_path: "uploads/report.pdf",
    media_type: "application/pdf",
    name: "report.pdf",
    size_bytes: 1024,
  },
  "result-block": {
    kind: "result-block",
    stdout: "ok",
    truncated: false,
  },
  working: {
    kind: "working",
    operation: "code_exec",
  },
  failure: {
    kind: "failure",
    operation: "code_execution",
    error_message: "boom",
  },
};

const EXPECTED_DATA_SLOT: Record<OutputContent["kind"], string> = {
  "inline-image": "inline-visual",
  "inline-chart": "inline-visual",
  "download-doc": "download-chip",
  "result-block": "result-block",
  working: "working-state",
  failure: "output-failure",
};

describe("Spec F4 T14 — structural invariants", () => {
  describe("1. dispatcher exhaustiveness", () => {
    it.each(Object.keys(VARIANT_PROBES) as Array<OutputContent["kind"]>)(
      "kind=%s produces a rendered output (no silent drop)",
      (kind) => {
        const probe = VARIANT_PROBES[kind];
        const { container } = renderWithIntl(
          <OutputDispatcher personaId="p1" output={probe} />,
        );
        const slot = EXPECTED_DATA_SLOT[kind];
        expect(
          container.querySelector(`[data-slot="${slot}"]`),
        ).toBeInTheDocument();
      },
    );

    it("the OutputContent type has exactly six variants (drift sentinel)", () => {
      const kinds = Object.keys(VARIANT_PROBES) as ReadonlyArray<
        OutputContent["kind"]
      >;
      // If a future spec adds a variant, both this count AND the dispatcher
      // switch + EXPECTED_DATA_SLOT must update — this test catches the drift.
      expect(kinds).toHaveLength(6);
    });
  });

  describe("2. 1 MB-stays-by-reference (F3 T22 mirror)", () => {
    it("a 1 MB produced_file is stored as workspace_path, never inlined", () => {
      const event: ChatEvent = {
        event: "tool_result",
        data: {
          tool_name: "code_execution",
          is_error: false,
          content: "ok",
          produced_files: [
            {
              path: "charts/big.png",
              size_bytes: 1024 * 1024,
              media_type: "image/png",
            },
          ],
        },
      };
      const outputs = chatSseToOutputContent(event);
      // Serialised OutputContent[] size is bounded — must NOT scale with
      // referenced file size. 1 MB reference must serialise to <500 bytes.
      const serialised = JSON.stringify(outputs);
      expect(serialised.length).toBeLessThan(500);
      // No `bytes` field appears anywhere — the discriminated union
      // doesn't carry inline payload by construction.
      expect(serialised).not.toContain('"bytes"');
      expect(serialised).not.toContain('"base64"');
    });

    it("100 × 5 MB files keep serialised output linear in count (not bytes)", () => {
      const event: ChatEvent = {
        event: "tool_result",
        data: {
          tool_name: "code_execution",
          is_error: false,
          content: "ok",
          produced_files: Array.from({ length: 100 }, (_, i) => ({
            path: `charts/chart-${i}.png`,
            size_bytes: 5 * 1024 * 1024,
            media_type: "image/png",
          })),
        },
      };
      const outputs = chatSseToOutputContent(event);
      const serialised = JSON.stringify(outputs);
      // 100 references × ~120 bytes per ≈ 12 KB. The cap tolerates path
      // variance; the structural point is "linear in count, NOT in
      // bytes_referenced" — 500 MB referenced must NOT bloat the wire.
      expect(serialised.length).toBeLessThan(20_000);
      expect(outputs).toHaveLength(100);
    });
  });

  describe("3. single-renderer-set (chat ↔ run normaliser parity)", () => {
    it.each([
      {
        label: "inline-chart (charts/ prefix + image/*)",
        produced: {
          path: "charts/q1.png",
          size_bytes: 100,
          media_type: "image/png",
        },
      },
      {
        label: "inline-image (uploads/ + image/*)",
        produced: {
          path: "uploads/abc.png",
          size_bytes: 200,
          media_type: "image/png",
        },
      },
      {
        label: "download-doc (PDF media-type)",
        produced: {
          path: "uploads/report.pdf",
          size_bytes: 300,
          media_type: "application/pdf",
        },
      },
    ])(
      "$label produces IDENTICAL OutputContent on chat + run transports",
      ({ produced }) => {
        const chatEvent: ChatEvent = {
          event: "tool_result",
          data: {
            tool_name: "code_execution",
            is_error: false,
            content: "ok",
            produced_files: [produced],
          },
        };
        const runEvent: RunEvent = {
          type: "tool_result",
          step: 0,
          data: {
            tool_name: "code_execution",
            is_error: false,
            content: "ok",
            produced_files: [produced],
          },
          timestamp: "2026-06-07T00:00:00Z",
        };
        const chatOut = chatSseToOutputContent(chatEvent);
        const runOut = runEventToOutputContent(runEvent);
        // Structural identity — transport-shape leakage stops at the
        // normaliser per D-09-1.
        expect(runOut).toEqual(chatOut);
      },
    );
  });

  describe("4. path-traversal swap-to-failure at the dispatcher", () => {
    it.each([
      "../etc/passwd",
      "uploads/../../etc/shadow",
      "charts/../uploads/spoof.png",
      "..",
    ])("path %s swaps to failure render (no inline-visual)", (badPath) => {
      const probe: OutputContent = {
        kind: "inline-image",
        workspace_path: badPath,
        media_type: "image/png",
        alt: "x",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={probe} />,
      );
      expect(
        container.querySelector('[data-slot="output-failure"]'),
      ).toBeInTheDocument();
      expect(container.querySelector('[data-slot="inline-visual"]')).toBeNull();
    });
  });

  describe("5. cross-surface consistency (criterion 6)", () => {
    it.each(Object.keys(VARIANT_PROBES) as Array<OutputContent["kind"]>)(
      "kind=%s renders the same data-slot regardless of consumer (chat or run)",
      (kind) => {
        const probe = VARIANT_PROBES[kind];
        // Render TWICE with two different React trees — emulating chat
        // (T10) + run (T11) consumers. The dispatcher is shared, so the
        // resulting data-slot MUST be identical.
        const first = renderWithIntl(
          <OutputDispatcher personaId="p1" output={probe} />,
        );
        const firstSlot = first.container
          .querySelector(`[data-slot="${EXPECTED_DATA_SLOT[kind]}"]`)
          ?.getAttribute("data-slot");
        first.unmount();
        const second = renderWithIntl(
          <OutputDispatcher personaId="different-persona" output={probe} />,
        );
        const secondSlot = second.container
          .querySelector(`[data-slot="${EXPECTED_DATA_SLOT[kind]}"]`)
          ?.getAttribute("data-slot");
        expect(firstSlot).toBe(secondSlot);
      },
    );
  });

  describe("6. dispatcher dispatch table matches R-F4-1 routing contract", () => {
    it.each([
      {
        label: "Spec 17 chart (charts/<id>.png + image/png) → inline-chart",
        path: "charts/abc.png",
        media_type: "image/png",
        expectedSlot: "inline-visual",
        expectedIntent: "chart",
      },
      {
        label:
          "Spec 15 generated image (uploads/<blake2b>.png + image/png) → inline-image",
        path: "uploads/abc.png",
        media_type: "image/png",
        expectedSlot: "inline-visual",
        expectedIntent: "image",
      },
      {
        label: "Spec 16 docx (uploads/<filename>.docx) → download-doc",
        path: "uploads/report.docx",
        media_type:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        expectedSlot: "download-chip",
      },
      {
        label: "Spec 16 pptx → download-doc",
        path: "uploads/x.pptx",
        media_type:
          "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        expectedSlot: "download-chip",
      },
      {
        label: "Spec 16 xlsx → download-doc",
        path: "uploads/x.xlsx",
        media_type:
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        expectedSlot: "download-chip",
      },
      {
        label: "Spec 16 pdf → download-doc",
        path: "uploads/x.pdf",
        media_type: "application/pdf",
        expectedSlot: "download-chip",
      },
    ])("$label", ({ path, media_type, expectedSlot, expectedIntent }) => {
      // Run the produced_file through the normaliser, then dispatch the
      // resulting OutputContent.
      const event: ChatEvent = {
        event: "tool_result",
        data: {
          tool_name: "code_execution",
          is_error: false,
          content: "ok",
          produced_files: [{ path, size_bytes: 1, media_type }],
        },
      };
      const [out] = chatSseToOutputContent(event);
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={out} />,
      );
      const slot = container.querySelector(`[data-slot="${expectedSlot}"]`);
      expect(slot).toBeInTheDocument();
      if (expectedIntent !== undefined) {
        expect(slot).toHaveAttribute("data-intent", expectedIntent);
      }
    });
  });

  describe("Zod boundary validation pins the OutputContent contract", () => {
    it.each(Object.keys(VARIANT_PROBES) as Array<OutputContent["kind"]>)(
      "kind=%s is accepted by the Zod schema",
      (kind) => {
        const probe = VARIANT_PROBES[kind];
        expect(outputContentSchema.safeParse(probe).success).toBe(true);
      },
    );

    it("an extra field on a variant is rejected (strict mode)", () => {
      expect(
        outputContentSchema.safeParse({
          ...VARIANT_PROBES["inline-image"],
          bytes: "rogue inline payload",
        }).success,
      ).toBe(false);
    });
  });
});
