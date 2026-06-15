"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import { sanitizeSvg } from "@/lib/sanitize";
import { RendererError, RendererLoading, RendererUnavailable } from "./_states";
import type { ArtifactRendererProps } from "./types";

/**
 * Mermaid renderer — client-side (D-28-3), lazy-loaded so the ~500 KB mermaid
 * bundle never touches the initial chat chunk.
 *
 * SECURITY (D-28-X-svg-sanitization): mermaid runs with `securityLevel:
 * "strict"` AND the produced SVG is passed through {@link sanitizeSvg}
 * (DOMPurify) before DOM injection — every SVG path is sanitized. Malformed
 * source surfaces as a render error, never a thrown exception.
 */
export function MermaidRenderer({
  personaId,
  workspacePath,
}: ArtifactRendererProps) {
  const t = useTranslations("chat.output.renderer");
  const { text, loading, error } = useAuthedArtifactText(
    personaId,
    workspacePath,
  );
  const [svg, setSvg] = useState<string | null>(null);
  const [renderFailed, setRenderFailed] = useState(false);

  useEffect(() => {
    if (text === null) return;
    let cancelled = false;
    setRenderFailed(false);
    setSvg(null);
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
        const id = `mmd-${crypto.randomUUID()}`;
        const { svg: out } = await mermaid.render(id, text);
        if (!cancelled) setSvg(sanitizeSvg(out));
      } catch {
        if (!cancelled) setRenderFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [text]);

  if (error) return <RendererError reason={error.message} />;
  if (loading && text === null) return <RendererLoading />;
  if (text === null) return <RendererUnavailable />;
  if (renderFailed) {
    return (
      <div
        className="p-4 text-sm text-destructive"
        role="alert"
        data-slot="renderer-mermaid-error"
      >
        {t("diagramError")}
      </div>
    );
  }
  if (svg === null) return <RendererLoading />;
  return (
    <div
      className="grid place-items-center overflow-auto p-4"
      data-slot="renderer-mermaid"
      // biome-ignore lint/security/noDangerouslySetInnerHtml: SVG sanitized via sanitizeSvg (DOMPurify) per D-28-X-svg-sanitization
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
