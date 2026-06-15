"use client";

import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import {
  RawSource,
  RendererError,
  RendererLoading,
  RendererUnavailable,
} from "./_states";
import type { ArtifactRendererProps } from "./types";

/** Plaintext / .log renderer — rendered === raw (plain `<pre>`). */
export function PlaintextRenderer({
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
  return <RawSource text={text} />;
}
