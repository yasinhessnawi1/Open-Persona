"use client";

import { lazy, Suspense } from "react";
import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import {
  RawSource,
  RendererError,
  RendererLoading,
  RendererUnavailable,
} from "./_states";
import { CodeRenderer } from "./code-renderer";
import { CsvRenderer } from "./csv-renderer";
import { HtmlRenderer } from "./html-renderer";
import { ImageRenderer } from "./image-renderer";
import { JsonRenderer } from "./json-renderer";
import { MarkdownRenderer } from "./markdown-renderer";
import { PlaintextRenderer } from "./plaintext-renderer";
import {
  type ArtifactRendererProps,
  isBinaryKind,
  type RendererKind,
} from "./types";

// Heavy renderers (mermaid ~500 KB, graphviz ~600 KB WASM, pdf worker) are
// code-split so they never touch the initial chat bundle (research risk #1).
const MermaidRenderer = lazy(() =>
  import("./mermaid-renderer").then((m) => ({ default: m.MermaidRenderer })),
);
const GraphvizRenderer = lazy(() =>
  import("./graphviz-renderer").then((m) => ({ default: m.GraphvizRenderer })),
);
const PdfRenderer = lazy(() =>
  import("./pdf-renderer").then((m) => ({ default: m.PdfRenderer })),
);

/** The rendered (non-raw) view for a given format. */
export function RenderedView({
  kind,
  ...props
}: ArtifactRendererProps & { kind: RendererKind }) {
  switch (kind) {
    case "markdown":
      return <MarkdownRenderer {...props} />;
    case "code":
      return <CodeRenderer {...props} />;
    case "plaintext":
      return <PlaintextRenderer {...props} />;
    case "json":
      return <JsonRenderer {...props} />;
    case "csv":
      return <CsvRenderer {...props} />;
    case "html":
      return <HtmlRenderer {...props} />;
    case "image":
      return <ImageRenderer {...props} />;
    case "mermaid":
      return (
        <Suspense fallback={<RendererLoading />}>
          <MermaidRenderer {...props} />
        </Suspense>
      );
    case "graphviz":
      return (
        <Suspense fallback={<RendererLoading />}>
          <GraphvizRenderer {...props} />
        </Suspense>
      );
    case "pdf":
      return (
        <Suspense fallback={<RendererLoading />}>
          <PdfRenderer {...props} />
        </Suspense>
      );
  }
}

/** The raw/source view — a `<pre>` of the fetched text. Binary formats have no
 *  distinct raw view (the panel never calls this for pdf/image). */
function RawArtifactView({ personaId, workspacePath }: ArtifactRendererProps) {
  const { text, loading, error } = useAuthedArtifactText(
    personaId,
    workspacePath,
  );
  if (error) return <RendererError reason={error.message} />;
  if (loading && text === null) return <RendererLoading />;
  if (text === null) return <RendererUnavailable />;
  return <RawSource text={text} />;
}

/** Top-level view chooser used by the panel: rendered vs raw. */
export function ArtifactView({
  kind,
  mode,
  ...props
}: ArtifactRendererProps & { kind: RendererKind; mode: "rendered" | "raw" }) {
  if (mode === "raw" && !isBinaryKind(kind)) {
    return <RawArtifactView {...props} />;
  }
  return <RenderedView kind={kind} {...props} />;
}
