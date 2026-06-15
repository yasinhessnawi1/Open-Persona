"use client";

import { useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import { useAuthedImageBlobUrl } from "@/lib/hooks/use-authed-image-blob-url";
import { RendererError, RendererLoading, RendererUnavailable } from "./_states";
import type { ArtifactRendererProps } from "./types";

// Worker source. The `new URL("pdfjs-dist/build/...", import.meta.url)` pattern
// does NOT resolve under Next 16 / Turbopack (module-not-found on the worker
// chunk, which breaks the whole renderer chain). Use a plain version-pinned CDN
// string (react-pdf's documented alternative) — a string URL, so the bundler
// never tries to resolve a module path. The version is read from the installed
// pdfjs (`pdfjs.version`) so the worker always matches the bundled core.
// (Vendoring the worker into /public is a v0.2 candidate to drop the CDN dep —
// MAINTENANCE renderer-version-watch row.)
pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

/** Inner react-pdf view — loaded via next/dynamic(ssr:false) by PdfRenderer. */
export default function PdfView({
  personaId,
  workspacePath,
}: ArtifactRendererProps) {
  const { src, loading, error } = useAuthedImageBlobUrl(
    personaId,
    workspacePath,
  );
  const [numPages, setNumPages] = useState(0);

  if (error) return <RendererError reason={error.message} />;
  if (loading && src === null) return <RendererLoading />;
  if (src === null) return <RendererUnavailable />;
  return (
    <div className="overflow-auto p-4" data-slot="renderer-pdf">
      <Document
        file={src}
        onLoadSuccess={(doc: { numPages: number }) => setNumPages(doc.numPages)}
        loading={<RendererLoading />}
      >
        {Array.from({ length: numPages }, (_, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: page order is stable for a loaded document
          <Page key={i} pageNumber={i + 1} className="mb-2" />
        ))}
      </Document>
    </div>
  );
}
