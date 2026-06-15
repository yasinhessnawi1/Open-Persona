"use client";

import { Markdown } from "@/components/ui/markdown";
import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import { RendererError, RendererLoading, RendererUnavailable } from "./_states";
import type { ArtifactRendererProps } from "./types";

/** Markdown renderer — reuses the rehype-sanitize-hardened `<Markdown>` (D-28-X-svg-sanitization). */
export function MarkdownRenderer({
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
  return (
    <div className="p-4" data-slot="renderer-markdown">
      <Markdown>{text}</Markdown>
    </div>
  );
}
