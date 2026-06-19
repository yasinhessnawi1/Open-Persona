"use client";

import {
  Download,
  FileCode,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
  FileType,
  Workflow,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { type ComponentType, useState } from "react";
import { useAuth } from "@/auth";
import { AuthedImage } from "@/components/ui/authed-image";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useFileRenderer } from "./file-renderer-context";
import { RenderedView } from "./renderers";
import { type RendererKind, rendererKindFor } from "./renderers/types";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

// D-28-7 / D-28-8 inline-affordance thresholds.
const IMAGE_INLINE_MAX_BYTES = 5 * 1024 * 1024; // 5 MB
const DIAGRAM_INLINE_MAX_BYTES = 100 * 1024; // 100 KB (source)

type LucideIcon = ComponentType<{
  className?: string;
  "aria-hidden"?: boolean;
}>;

const ICON_BY_KIND: Record<RendererKind, LucideIcon> = {
  markdown: FileText,
  code: FileCode,
  plaintext: FileText,
  json: FileJson,
  csv: FileSpreadsheet,
  html: FileCode,
  pdf: FileType,
  image: FileImage,
  mermaid: Workflow,
  graphviz: Workflow,
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface FileCardProps {
  personaId: string;
  workspacePath: string;
  mediaType: string;
  name: string;
  sizeBytes: number;
  renderedInline: boolean;
  className?: string;
}

/**
 * Spec 28 — inline file card (§2.2), mirroring the Anthropic Claude pattern:
 * type icon + filename + type·size + download button, click-anywhere opens the
 * right-panel renderer (D-28-5). Images < 5 MB show a thumbnail (D-28-7) and
 * Mermaid/Graphviz sources < 100 KB render an inline SVG (D-28-8) above the card.
 */
export function FileCard({
  personaId,
  workspacePath,
  mediaType,
  name,
  sizeBytes,
  renderedInline,
  className,
}: FileCardProps) {
  const t = useTranslations("chat.output.downloadChip");
  const tc = useTranslations("chat.output.fileCard");
  const { open } = useFileRenderer();
  const { getToken } = useAuth();
  const [downloading, setDownloading] = useState(false);

  const kind = rendererKindFor(mediaType, name);
  const Icon = ICON_BY_KIND[kind];

  const showImageThumb =
    renderedInline && kind === "image" && sizeBytes < IMAGE_INLINE_MAX_BYTES;
  const showDiagramInline =
    renderedInline &&
    (kind === "mermaid" || kind === "graphviz") &&
    sizeBytes < DIAGRAM_INLINE_MAX_BYTES;

  function openPanel() {
    open({ workspacePath, mediaType, name });
  }

  async function handleDownload(): Promise<void> {
    if (downloading) return;
    setDownloading(true);
    let objectUrl: string | null = null;
    try {
      const token = await getToken(
        TEMPLATE ? { template: TEMPLATE } : undefined,
      );
      const res = await fetch(
        `${API}/v1/personas/${encodeURIComponent(personaId)}/uploads/${workspacePath}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} },
      );
      if (!res.ok) throw new Error(`download ${res.status}`);
      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch {
      // Surfaced via the disabled state clearing; download is best-effort here.
    } finally {
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
      setDownloading(false);
    }
  }

  return (
    <div
      className={cn("flex w-fit max-w-sm flex-col gap-1.5", className)}
      data-slot="file-card-wrap"
    >
      {showImageThumb && (
        <button
          type="button"
          onClick={openPanel}
          aria-label={tc("open", { filename: name })}
        >
          <AuthedImage
            personaId={personaId}
            workspacePath={workspacePath}
            mediaType={mediaType}
            alt={name}
            className="max-h-60 max-w-80 rounded-md"
          />
        </button>
      )}
      {showDiagramInline && (
        <button
          type="button"
          onClick={openPanel}
          aria-label={tc("open", { filename: name })}
          className="overflow-hidden rounded-md border border-border"
        >
          <div className="max-h-60 max-w-80">
            <RenderedView
              kind={kind}
              personaId={personaId}
              workspacePath={workspacePath}
              mediaType={mediaType}
            />
          </div>
        </button>
      )}

      {/* biome-ignore lint/a11y/useSemanticElements: interactive styled container; cannot be a real <button> because it nests the download <button> — keyboard + role + aria-label provide the a11y contract */}
      <Card
        size="sm"
        // `flex-row` is REQUIRED: the Card base is `flex flex-col`, so without it
        // the row collapses to a tall, centered vertical stack (the "big card"
        // bug). This is the compact .v-file-style row: icon · name/meta · download.
        className={cn(
          "flex flex-row cursor-pointer items-center gap-2 px-3 py-2 hover:bg-muted/50",
        )}
        data-slot="file-card"
        data-media-type={mediaType}
        onClick={openPanel}
        role="button"
        tabIndex={0}
        aria-label={tc("open", { filename: name })}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPanel();
          }
        }}
      >
        <span className="text-muted-foreground">
          <Icon className="size-4 shrink-0" aria-hidden />
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="type-ui truncate text-foreground" title={name}>
            {name}
          </span>
          <span className="type-caption text-muted-foreground">
            {kind.toUpperCase()} · {formatSize(sizeBytes)}
          </span>
        </div>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            void handleDownload();
          }}
          disabled={downloading}
          aria-label={t("download", { filename: name })}
          className={cn(
            "grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground",
            "hover:bg-muted hover:text-foreground disabled:opacity-50",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          )}
          data-slot="file-card-download"
        >
          <Download className="size-4" aria-hidden />
        </button>
      </Card>
    </div>
  );
}
