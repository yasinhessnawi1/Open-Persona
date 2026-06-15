"use client";

import { lazy, Suspense } from "react";
import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import { RendererError, RendererLoading, RendererUnavailable } from "./_states";
import { type ArtifactRendererProps, codeLangFor } from "./types";

// Reuse the F4 Shiki highlighter (lazy — the highlighter chunk loads on demand).
const HighlightedCode = lazy(() => import("../output/highlighted-code"));

/** Code renderer — syntax-highlighted via the existing Shiki component. */
export function CodeRenderer({
  personaId,
  workspacePath,
}: ArtifactRendererProps) {
  const { text, loading, error } = useAuthedArtifactText(
    personaId,
    workspacePath,
  );
  if (error) return <RendererError reason={error.message} />;
  if (loading && text === null) return <RendererLoading />;
  if (text === null) return <RendererUnavailable />;
  const name = workspacePath.split("/").pop() ?? workspacePath;
  return (
    <div className="p-4" data-slot="renderer-code">
      <Suspense fallback={<RendererLoading />}>
        <HighlightedCode code={text} lang={codeLangFor(name)} />
      </Suspense>
    </div>
  );
}
