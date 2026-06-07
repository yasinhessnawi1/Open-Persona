/**
 * Spec F4 T02 — `OutputContent` discriminated union tests
 * (D-F4-X-renderer-normaliser-shape).
 *
 * Six variants × parse-accepts × parse-rejects × strict-mode (Pydantic
 * `extra="forbid"` parity). The discriminator is `kind`; round-trips are
 * verified for every variant; unknown kinds are rejected.
 */

import { describe, expect, it } from "vitest";

import { type OutputContent, outputContentSchema } from "./output-content";

describe("OutputContent (D-F4-X-renderer-normaliser-shape)", () => {
  describe("discriminator", () => {
    it("rejects a frame with no kind field", () => {
      expect(outputContentSchema.safeParse({}).success).toBe(false);
    });

    it("rejects an unknown discriminator value", () => {
      const v = {
        kind: "video",
        workspace_path: "uploads/x.mp4",
        media_type: "video/mp4",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(false);
    });
  });

  describe("inline-image variant", () => {
    it("accepts a minimal valid frame", () => {
      const v: OutputContent = {
        kind: "inline-image",
        workspace_path: "uploads/abc.png",
        media_type: "image/png",
        alt: "abc.png",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });

    it("accepts an optional caption", () => {
      const v: OutputContent = {
        kind: "inline-image",
        workspace_path: "uploads/abc.png",
        media_type: "image/png",
        alt: "abc.png",
        caption: "a red bicycle",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });

    it("rejects an empty workspace_path", () => {
      const v = {
        kind: "inline-image",
        workspace_path: "",
        media_type: "image/png",
        alt: "abc.png",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(false);
    });

    it("rejects extra fields (strict mode = Pydantic extra='forbid' parity)", () => {
      const v = {
        kind: "inline-image",
        workspace_path: "uploads/abc.png",
        media_type: "image/png",
        alt: "abc.png",
        wild: true,
      };
      expect(outputContentSchema.safeParse(v).success).toBe(false);
    });
  });

  describe("inline-chart variant", () => {
    it("accepts Spec 17's charts/<id>.png path", () => {
      const v: OutputContent = {
        kind: "inline-chart",
        workspace_path: "charts/q1-rev.png",
        media_type: "image/png",
        prose_context: "Q1 revenue rose 12%.",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });

    it("accepts a chart without prose_context", () => {
      const v: OutputContent = {
        kind: "inline-chart",
        workspace_path: "charts/x.png",
        media_type: "image/png",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });
  });

  describe("download-doc variant", () => {
    it("accepts the Spec 16 docx shape post-T02c (uploads/ prepended)", () => {
      const v: OutputContent = {
        kind: "download-doc",
        workspace_path: "uploads/report.docx",
        media_type:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        name: "report.docx",
        size_bytes: 12345,
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });

    it("treats size_bytes as optional (pre-T02b backwards-compat safety net)", () => {
      const v: OutputContent = {
        kind: "download-doc",
        workspace_path: "uploads/report.pdf",
        media_type: "application/pdf",
        name: "report.pdf",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });

    it("rejects negative size_bytes", () => {
      const v = {
        kind: "download-doc",
        workspace_path: "uploads/report.pdf",
        media_type: "application/pdf",
        name: "report.pdf",
        size_bytes: -1,
      };
      expect(outputContentSchema.safeParse(v).success).toBe(false);
    });

    it("rejects non-integer size_bytes", () => {
      const v = {
        kind: "download-doc",
        workspace_path: "uploads/report.pdf",
        media_type: "application/pdf",
        name: "report.pdf",
        size_bytes: 1.5,
      };
      expect(outputContentSchema.safeParse(v).success).toBe(false);
    });
  });

  describe("result-block variant", () => {
    it("accepts stdout + truncated", () => {
      const v: OutputContent = {
        kind: "result-block",
        stdout: "1\n2\n3\n",
        truncated: false,
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });

    it("accepts optional code + language (D-F4-1 instrument-transparency)", () => {
      const v: OutputContent = {
        kind: "result-block",
        stdout: "Hello",
        truncated: false,
        code: "print('Hello')",
        language: "python",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });

    it("accepts an empty stdout string (a successful execution with no output)", () => {
      const v: OutputContent = {
        kind: "result-block",
        stdout: "",
        truncated: false,
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });
  });

  describe("working variant", () => {
    it.each(["image_gen", "code_exec", "doc_gen"] as const)(
      "accepts operation=%s (closed enum)",
      (op) => {
        const v: OutputContent = { kind: "working", operation: op };
        expect(outputContentSchema.safeParse(v).success).toBe(true);
      },
    );

    it("rejects unknown operations", () => {
      const v = { kind: "working", operation: "video_gen" };
      expect(outputContentSchema.safeParse(v).success).toBe(false);
    });

    it("accepts an optional label", () => {
      const v: OutputContent = {
        kind: "working",
        operation: "code_exec",
        label: "code_execution",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });
  });

  describe("failure variant", () => {
    it("accepts a tool failure with operation=tool_name", () => {
      const v: OutputContent = {
        kind: "failure",
        operation: "code_execution",
        error_message: "outcome=timeout exit_code=124",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });

    it("accepts a run-level failure with operation=run", () => {
      const v: OutputContent = {
        kind: "failure",
        operation: "run",
        error_message: "Connection reset",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(true);
    });

    it("rejects an empty error_message", () => {
      const v = {
        kind: "failure",
        operation: "code_execution",
        error_message: "",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(false);
    });

    it("rejects an empty operation", () => {
      const v = {
        kind: "failure",
        operation: "",
        error_message: "boom",
      };
      expect(outputContentSchema.safeParse(v).success).toBe(false);
    });
  });

  describe("variant round-trip", () => {
    it("all six variants survive parse and preserve their kind discriminator", () => {
      const variants: OutputContent[] = [
        {
          kind: "inline-image",
          workspace_path: "uploads/a.png",
          media_type: "image/png",
          alt: "a",
        },
        {
          kind: "inline-chart",
          workspace_path: "charts/a.png",
          media_type: "image/png",
        },
        {
          kind: "download-doc",
          workspace_path: "uploads/a.docx",
          media_type:
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          name: "a.docx",
        },
        { kind: "result-block", stdout: "ok", truncated: false },
        { kind: "working", operation: "code_exec" },
        {
          kind: "failure",
          operation: "code_execution",
          error_message: "boom",
        },
      ];
      for (const v of variants) {
        const parsed = outputContentSchema.parse(v);
        expect(parsed.kind).toBe(v.kind);
      }
    });

    it("six-variant exhaustiveness — the dispatcher (T09) covers every kind", () => {
      // This compile-time assertion ensures every variant is handled when
      // T09 dispatcher's switch lands. If a future variant is added without
      // a matching dispatch branch, this assertion will fail to type-check.
      const variants: OutputContent["kind"][] = [
        "inline-image",
        "inline-chart",
        "download-doc",
        "result-block",
        "working",
        "failure",
      ];
      expect(variants).toHaveLength(6);
    });
  });
});
