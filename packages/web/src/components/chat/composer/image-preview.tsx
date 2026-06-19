"use client";

import { FileImage, Loader2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { useObjectURL } from "@/lib/hooks/use-object-url";
import { cn } from "@/lib/utils";
import type { ImageAttachment } from "./attach-state";

/**
 * Spec 35 — a PLAIN chip for a pending image attachment (replaces the big
 * thumbnail preview). Mirrors `<DocumentChip>`: icon + filename + remove, with
 * an upload spinner / error ring. The actual image preview lives in the Files
 * viewer + the sent message bubble — the composer just shows a compact chip.
 */
export function ComposerImageChip({
  attachment,
  onRemove,
}: ComposerImagePreviewProps) {
  const t = useTranslations("chat.composer");
  const isError = attachment.state === "error";
  const isBusy =
    attachment.state === "uploading" || attachment.state === "pending";

  return (
    <Card
      size="sm"
      className={cn(
        "flex w-fit max-w-xs flex-row items-center gap-2 px-3 py-2",
        isError && "ring-2 ring-destructive",
      )}
      data-slot="composer-image-chip"
    >
      <span className="text-muted-foreground">
        {isBusy ? (
          <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
        ) : (
          <FileImage className="size-4 shrink-0" aria-hidden />
        )}
      </span>
      <span
        className="type-ui truncate text-foreground"
        title={attachment.file.name}
      >
        {attachment.file.name}
      </span>
      {isError ? (
        <span className="type-caption text-destructive" role="alert">
          {attachment.detail}
        </span>
      ) : null}
      <button
        type="button"
        onClick={() => onRemove(attachment.id)}
        aria-label={t("attach.remove")}
        className={cn(
          "grid size-6 shrink-0 place-items-center rounded-full",
          "text-muted-foreground hover:bg-muted hover:text-foreground",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        )}
      >
        <X className="size-3" aria-hidden />
      </button>
    </Card>
  );
}

/**
 * F3 — composer image preview thumbnail (T09).
 *
 * Per-image card showing the thumbnail (via the `useObjectURL` hook —
 * D-F3-X-preview-cleanup-discipline) + remove affordance + the current
 * upload-state cue (T16 follow-up wires the progress bar / error ring).
 *
 * F3-local per D-F3-X-preview-placement; promote to F2 only on F4/F5
 * second-consumer reuse. Composes F2's `<Card>` + F2 tokens via
 * Tailwind utility classes (no literal design values per `check:no-literals`).
 */
export interface ComposerImagePreviewProps {
  attachment: ImageAttachment;
  /** Remove this image from the composer state. */
  onRemove: (id: string) => void;
}

export function ComposerImagePreview({
  attachment,
  onRemove,
}: ComposerImagePreviewProps) {
  const t = useTranslations("chat.composer");
  const url = useObjectURL(attachment.file);

  const isError = attachment.state === "error";
  const isUploading = attachment.state === "uploading";

  return (
    <Card
      size="sm"
      className={cn(
        "relative size-20 overflow-hidden p-0",
        isError && "ring-2 ring-destructive",
      )}
      aria-label={t("upload.uploaded", { filename: attachment.file.name })}
    >
      {url ? (
        // biome-ignore lint/performance/noImgElement: blob: URLs can't go through next/image
        <img
          src={url}
          alt={attachment.file.name}
          className="size-full object-cover"
        />
      ) : (
        // Skeleton placeholder before the object URL resolves (one tick).
        <div className="size-full animate-pulse bg-muted" />
      )}

      {/* Upload-state overlay (T16 elaborates per-state visuals). */}
      {isUploading ? (
        <div className="absolute inset-x-0 bottom-0 h-1 bg-muted">
          <div
            className="h-full bg-primary transition-all"
            style={{
              width:
                typeof attachment.progress === "number"
                  ? `${Math.round(attachment.progress * 100)}%`
                  : "100%",
            }}
            aria-hidden
          />
        </div>
      ) : null}

      <button
        type="button"
        onClick={() => onRemove(attachment.id)}
        aria-label={t("attach.remove")}
        className={cn(
          "absolute top-1 right-1 grid size-5 place-items-center rounded-full",
          "bg-background/80 text-foreground backdrop-blur",
          "hover:bg-background focus-visible:outline-2",
          "focus-visible:outline-offset-2 focus-visible:outline-ring",
        )}
      >
        <X className="size-3" aria-hidden />
      </button>

      {isError ? (
        <p
          className="absolute right-0 bottom-0 left-0 truncate bg-destructive/90 px-1 type-caption text-destructive-foreground"
          role="alert"
        >
          {attachment.detail}
        </p>
      ) : null}
    </Card>
  );
}
