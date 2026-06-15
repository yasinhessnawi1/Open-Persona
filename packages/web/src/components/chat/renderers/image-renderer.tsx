"use client";

import { AuthedImage } from "@/components/ui/authed-image";
import type { ArtifactRendererProps } from "./types";

/** Image renderer — reuses the authed-image blob-URL component. */
export function ImageRenderer({
  personaId,
  workspacePath,
  mediaType,
}: ArtifactRendererProps) {
  const name = workspacePath.split("/").pop() ?? workspacePath;
  return (
    <div className="grid place-items-center p-4" data-slot="renderer-image">
      <AuthedImage
        personaId={personaId}
        workspacePath={workspacePath}
        mediaType={mediaType}
        alt={name}
        className="max-h-full max-w-full"
      />
    </div>
  );
}
