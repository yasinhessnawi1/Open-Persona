"use client";

import { useAuth } from "@clerk/nextjs";
import {
  Download,
  FileSpreadsheet,
  FileText,
  FileType,
  Presentation,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { type ComponentType, type ReactNode, useState } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * Spec F4 T06 — `<DownloadChip>`.
 *
 * Surfaces a Spec 16 generated document (docx/pptx/xlsx/pdf) as a
 * download affordance: format icon + filename + extension/size label +
 * Bearer-auth download button. Mirrors F3's `<DocumentChip>` voice
 * (D-F3-2 chip + composed F2 `<Card>`) on the OUTPUT side — F3 surfaces
 * user-uploaded documents (with remove); F4 surfaces persona-generated
 * documents (with download).
 *
 * Download flow:
 *   1. User clicks the download button.
 *   2. Fetch `/v1/personas/:id/uploads/:ref` with `Authorization: Bearer`
 *      (D-F3-X-image-serve-auth pattern — browsers don't send Authorization
 *      on `<a download>` so we cannot use a plain anchor href).
 *   3. Convert response → blob → object URL → trigger native download via
 *      programmatic `<a>` click → revoke the object URL.
 *
 * Mirrors the F3 byte-loader hook's error/abort semantics inline: errors
 * surface as `error` state with a destructive ring; the button is
 * disabled while a fetch is in flight (no double-trigger).
 *
 * Post-T02c (D-F4-X-bare-ref-resolution), Spec 16 docs persist into
 * `uploads/<filename>.<ext>` so the slash-aware resolver at
 * `image_service.fetch:300` serves them correctly.
 */

/**
 * Pattern → icon component (NOT pre-instantiated JSX). Storing component
 * refs keeps the table allocation-free at module load and dodges the
 * "JSX in array iterable needs a key" lint that fires when biome sees
 * JSX elements stored in an array literal — only the matching icon is
 * actually instantiated, so keys are irrelevant here.
 */
type LucideIcon = ComponentType<{
  className?: string;
  "aria-hidden"?: boolean;
}>;

const MEDIA_TYPE_ICONS: Array<[RegExp, LucideIcon]> = [
  [/^application\/pdf/, FileType],
  [/wordprocessingml/, FileText],
  [/spreadsheetml/, FileSpreadsheet],
  [/presentationml/, Presentation],
];

function iconForMediaType(mediaType: string): ReactNode {
  for (const [pattern, Icon] of MEDIA_TYPE_ICONS) {
    if (pattern.test(mediaType)) {
      return <Icon className="size-4 shrink-0" aria-hidden />;
    }
  }
  return <FileText className="size-4 shrink-0" aria-hidden />;
}

/**
 * Resolve a short uppercase extension label for the chip's caption row.
 * Prefer the filename extension when present; fall back to a small
 * media-type → extension table for common Spec 16 outputs.
 */
function extLabel(mediaType: string, name: string): string {
  const dot = name.lastIndexOf(".");
  if (dot >= 0 && dot < name.length - 1) {
    return name.slice(dot + 1).toUpperCase();
  }
  if (mediaType === "application/pdf") return "PDF";
  if (mediaType.includes("wordprocessingml")) return "DOCX";
  if (mediaType.includes("spreadsheetml")) return "XLSX";
  if (mediaType.includes("presentationml")) return "PPTX";
  return "FILE";
}

function formatSize(bytes: number | undefined): string {
  if (bytes === undefined) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface DownloadChipProps {
  personaId: string;
  workspacePath: string;
  mediaType: string;
  /** Display name + filename for the `<a download>` attribute. */
  name: string;
  /** From `produced_files[].size_bytes` (D-F4-X-event-kind-for-produced-files). */
  sizeBytes?: number;
  className?: string;
}

export function DownloadChip({
  personaId,
  workspacePath,
  mediaType,
  name,
  sizeBytes,
  className,
}: DownloadChipProps) {
  const t = useTranslations("chat.output.downloadChip");
  const { getToken } = useAuth();
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function handleDownload(): Promise<void> {
    if (downloading) return;
    setDownloading(true);
    setError(null);
    let objectUrl: string | null = null;
    try {
      const token = await getToken(
        TEMPLATE !== undefined ? { template: TEMPLATE } : undefined,
      );
      const res = await fetch(
        `${API}/v1/personas/${encodeURIComponent(personaId)}/uploads/${workspacePath}`,
        {
          headers: token !== null ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      if (!res.ok) throw new Error(`download fetch ${res.status}`);
      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
      setDownloading(false);
    }
  }

  const ext = extLabel(mediaType, name);
  const sizeLabel = formatSize(sizeBytes);

  const buttonAriaLabel = downloading
    ? t("downloading", { filename: name })
    : error !== null
      ? t("failed", { reason: error.message })
      : t("download", { filename: name });

  return (
    <Card
      size="sm"
      className={cn(
        "flex max-w-md items-center gap-2 px-3 py-2",
        error !== null && "ring-2 ring-destructive",
        className,
      )}
      data-slot="download-chip"
      data-media-type={mediaType}
    >
      <span className="text-muted-foreground">
        {iconForMediaType(mediaType)}
      </span>

      <div className="flex min-w-0 flex-1 flex-col">
        <span className="type-ui truncate text-foreground" title={name}>
          {name}
        </span>
        <span className="type-caption text-muted-foreground">
          {ext}
          {sizeLabel.length > 0 ? ` · ${sizeLabel}` : ""}
        </span>
      </div>

      <button
        type="button"
        onClick={() => void handleDownload()}
        disabled={downloading}
        aria-label={buttonAriaLabel}
        className={cn(
          "grid size-8 shrink-0 place-items-center rounded-full",
          "text-muted-foreground hover:bg-muted hover:text-foreground",
          "focus-visible:outline-2 focus-visible:outline-offset-2",
          "focus-visible:outline-ring",
          "disabled:opacity-50",
        )}
        data-slot="download-chip-trigger"
      >
        <Download className="size-4" aria-hidden />
      </button>
    </Card>
  );
}
