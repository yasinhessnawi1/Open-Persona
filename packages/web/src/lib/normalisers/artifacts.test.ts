import { describe, expect, it } from "vitest";
import type { ToolResultData } from "@/lib/sse-types";
import { classifyArtifact, projectToolResult } from "./_classify";

// Spec 28 — ToolResult.artifacts → file-card (unified rich-output path).

describe("classifyArtifact", () => {
  it("maps an artifact to a file-card with derived name + mime", () => {
    const out = classifyArtifact({
      workspace_path: "uploads/abc.md",
      mime_type: "text/markdown",
      size_bytes: 2048,
      rendered_inline: false,
    });
    expect(out).toEqual({
      kind: "file-card",
      workspace_path: "uploads/abc.md",
      media_type: "text/markdown",
      name: "abc.md",
      size_bytes: 2048,
      rendered_inline: false,
    });
  });
});

describe("projectToolResult — artifacts precedence", () => {
  it("prefers artifacts over produced_files when both present", () => {
    const data: ToolResultData = {
      tool_name: "generate_image",
      is_error: false,
      content: "made an image",
      produced_files: [
        { path: "charts/x.png", size_bytes: 10, media_type: "image/png" },
      ],
      artifacts: [
        {
          workspace_path: "uploads/x.png",
          mime_type: "image/png",
          size_bytes: 10,
          rendered_inline: true,
        },
      ],
    };
    const out = projectToolResult(data);
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe("file-card");
  });

  it("falls back to produced_files when artifacts absent (legacy path)", () => {
    const data: ToolResultData = {
      tool_name: "code_execution",
      is_error: false,
      content: "ran",
      produced_files: [
        { path: "charts/x.png", size_bytes: 10, media_type: "image/png" },
      ],
    };
    const out = projectToolResult(data);
    expect(out[0].kind).toBe("inline-chart");
  });

  it("emits a file-card per artifact", () => {
    const data: ToolResultData = {
      tool_name: "code_execution",
      is_error: false,
      content: "ran",
      artifacts: [
        {
          workspace_path: "uploads/a.png",
          mime_type: "image/png",
          size_bytes: 1,
          rendered_inline: true,
        },
        {
          workspace_path: "uploads/b.csv",
          mime_type: "text/csv",
          size_bytes: 2,
          rendered_inline: false,
        },
      ],
    };
    const out = projectToolResult(data);
    expect(out).toHaveLength(2);
    expect(out.every((o) => o.kind === "file-card")).toBe(true);
  });

  it("is_error still wins over artifacts", () => {
    const data: ToolResultData = {
      tool_name: "render_diagram",
      is_error: true,
      content: "boom",
      artifacts: [
        {
          workspace_path: "uploads/x.mmd",
          mime_type: "text/vnd.mermaid",
          size_bytes: 1,
          rendered_inline: true,
        },
      ],
    };
    const out = projectToolResult(data);
    expect(out[0].kind).toBe("failure");
  });
});
