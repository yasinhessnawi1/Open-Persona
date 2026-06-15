/**
 * Spec 28 — right-panel renderer contract + format detection.
 *
 * Each format renderer implements the RENDERED view only; the panel handles the
 * RAW view uniformly (a `<pre>` of the fetched source for text formats; binary
 * formats — pdf/image — have no distinct raw view). Renderers fetch their own
 * bytes via the shared authed-artifact hooks (D-28-10: reuse `uploads/:ref`).
 */

/** The ten v0.1 render formats (D-28-1). */
export type RendererKind =
  | "markdown"
  | "code"
  | "plaintext"
  | "json"
  | "csv"
  | "html"
  | "pdf"
  | "image"
  | "mermaid"
  | "graphviz";

export interface ArtifactRendererProps {
  personaId: string;
  workspacePath: string;
  mediaType: string;
}

/** Binary formats have no separate raw/source view (raw === rendered). */
export function isBinaryKind(kind: RendererKind): boolean {
  return kind === "pdf" || kind === "image";
}

const _CODE_EXTS = new Set([
  "py",
  "ts",
  "tsx",
  "js",
  "jsx",
  "go",
  "rs",
  "java",
  "c",
  "h",
  "cpp",
  "hpp",
  "cc",
  "rb",
  "sh",
  "bash",
  "sql",
  "yaml",
  "yml",
  "toml",
  "css",
  "scss",
  "kt",
]);

/** Map a code-file extension to a Shiki language hint. */
export function codeLangFor(name: string): string {
  const ext = name.includes(".")
    ? name.split(".").pop()?.toLowerCase()
    : undefined;
  return ext ?? "text";
}

/**
 * Resolve the renderer kind from the media type (preferred) then the filename
 * extension (D-28-X-render-diagram-mime makes diagram dispatch unambiguous via
 * the specific text/vnd.* media types).
 */
export function rendererKindFor(mediaType: string, name: string): RendererKind {
  switch (mediaType) {
    case "text/markdown":
      return "markdown";
    case "text/vnd.mermaid":
      return "mermaid";
    case "text/vnd.graphviz":
      return "graphviz";
    case "text/csv":
      return "csv";
    case "application/json":
      return "json";
    case "text/html":
      return "html";
    case "application/pdf":
      return "pdf";
  }
  if (mediaType.startsWith("image/")) return "image";

  const ext = name.includes(".")
    ? (name.split(".").pop()?.toLowerCase() ?? "")
    : "";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "mmd") return "mermaid";
  if (ext === "dot" || ext === "gv") return "graphviz";
  if (ext === "csv") return "csv";
  if (ext === "json") return "json";
  if (ext === "html" || ext === "htm") return "html";
  if (ext === "pdf") return "pdf";
  if (_CODE_EXTS.has(ext)) return "code";
  return "plaintext";
}
