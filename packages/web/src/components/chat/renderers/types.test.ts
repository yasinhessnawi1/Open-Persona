import { describe, expect, it } from "vitest";
import { isBinaryKind, rendererKindFor } from "./types";

describe("rendererKindFor", () => {
  it("dispatches diagrams by the specific MIME types (D-28-X-render-diagram-mime)", () => {
    expect(rendererKindFor("text/vnd.mermaid", "d.mmd")).toBe("mermaid");
    expect(rendererKindFor("text/vnd.graphviz", "d.dot")).toBe("graphviz");
  });

  it("maps the core media types", () => {
    expect(rendererKindFor("text/markdown", "x.md")).toBe("markdown");
    expect(rendererKindFor("application/json", "x.json")).toBe("json");
    expect(rendererKindFor("text/csv", "x.csv")).toBe("csv");
    expect(rendererKindFor("text/html", "x.html")).toBe("html");
    expect(rendererKindFor("application/pdf", "x.pdf")).toBe("pdf");
    expect(rendererKindFor("image/png", "x.png")).toBe("image");
  });

  it("falls back to extension when the media type is generic", () => {
    expect(rendererKindFor("application/octet-stream", "main.py")).toBe("code");
    expect(rendererKindFor("application/octet-stream", "notes.txt")).toBe(
      "plaintext",
    );
    expect(rendererKindFor("application/octet-stream", "graph.dot")).toBe(
      "graphviz",
    );
  });

  it("isBinaryKind marks pdf/image only", () => {
    expect(isBinaryKind("pdf")).toBe(true);
    expect(isBinaryKind("image")).toBe(true);
    expect(isBinaryKind("markdown")).toBe(false);
    expect(isBinaryKind("mermaid")).toBe(false);
  });
});
