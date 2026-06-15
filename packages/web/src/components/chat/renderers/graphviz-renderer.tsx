"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import { sanitizeSvg } from "@/lib/sanitize";
import { RendererError, RendererLoading, RendererUnavailable } from "./_states";
import type { ArtifactRendererProps } from "./types";

/**
 * Graphviz DOT renderer — client-side WASM (D-28-4), lazy-loaded so the ~600 KB
 * WASM binary never touches the initial chat chunk.
 *
 * SECURITY (D-28-X-svg-sanitization): the WASM-produced SVG is passed through
 * {@link sanitizeSvg} (DOMPurify) before DOM injection. Malformed DOT surfaces
 * as a render error, never a thrown exception.
 */
export function GraphvizRenderer({
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
        const { Graphviz } = await import("@hpcc-js/wasm-graphviz");
        const graphviz = await Graphviz.load();
        const out = graphviz.dot(text);
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
        data-slot="renderer-graphviz-error"
      >
        {t("diagramError")}
      </div>
    );
  }
  if (svg === null) return <RendererLoading />;
  return (
    <div
      className="grid place-items-center overflow-auto p-4"
      data-slot="renderer-graphviz"
      // biome-ignore lint/security/noDangerouslySetInnerHtml: SVG sanitized via sanitizeSvg (DOMPurify) per D-28-X-svg-sanitization
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
