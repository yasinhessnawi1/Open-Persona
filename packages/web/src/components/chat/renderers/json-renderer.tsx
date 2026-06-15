"use client";

import { defaultStyles, JsonView } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";
import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import {
  RawSource,
  RendererError,
  RendererLoading,
  RendererUnavailable,
} from "./_states";
import type { ArtifactRendererProps } from "./types";

/** JSON renderer — collapsible tree via react-json-view-lite (zero-dep, MIT).
 *  Malformed JSON falls back to the raw source view. */
export function JsonRenderer({
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
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return <RawSource text={text} />;
  }
  return (
    <div className="p-4 text-xs" data-slot="renderer-json">
      <JsonView data={parsed as object} style={defaultStyles} />
    </div>
  );
}
