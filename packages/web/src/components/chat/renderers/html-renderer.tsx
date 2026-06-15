"use client";

import { useMemo } from "react";
import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import { sanitizeHtml } from "@/lib/sanitize";
import { RendererError, RendererLoading, RendererUnavailable } from "./_states";
import type { ArtifactRendererProps } from "./types";

/**
 * HTML renderer — DOUBLE-defended (D-28-X-svg-sanitization):
 *   1. DOMPurify sanitizes the source ({@link sanitizeHtml}) before injection.
 *   2. A locked `<iframe sandbox="">` (NO `allow-scripts`, NO
 *      `allow-same-origin` — the combination that defeats the sandbox) renders
 *      it in a unique, script-less origin.
 */
export function HtmlRenderer({
  personaId,
  workspacePath,
}: ArtifactRendererProps) {
  const { text, loading, error } = useAuthedArtifactText(
    personaId,
    workspacePath,
  );
  const safe = useMemo(
    () => (text === null ? null : sanitizeHtml(text)),
    [text],
  );

  if (error) return <RendererError reason={error.message} />;
  if (loading && text === null) return <RendererLoading />;
  if (safe === null) return <RendererUnavailable />;
  return (
    <iframe
      // sandbox="" → fully locked: no scripts, opaque origin. Defense-in-depth
      // on top of the DOMPurify pass.
      sandbox=""
      srcDoc={safe}
      title={workspacePath.split("/").pop() ?? workspacePath}
      className="h-full min-h-96 w-full border-0"
      data-slot="renderer-html"
    />
  );
}
