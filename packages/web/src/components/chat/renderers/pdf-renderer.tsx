"use client";

import dynamic from "next/dynamic";
import { RendererLoading } from "./_states";
import type { ArtifactRendererProps } from "./types";

// react-pdf touches browser-only DOM APIs at import time → ssr:false.
const PdfView = dynamic(() => import("./_pdf-view"), {
  ssr: false,
  loading: () => <RendererLoading />,
});

/** PDF renderer — pdf.js (via react-pdf), client-only. */
export function PdfRenderer(props: ArtifactRendererProps) {
  return <PdfView {...props} />;
}
