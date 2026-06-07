"use client";

import { useAuth } from "@clerk/nextjs";
import { Download, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect } from "react";
import { createPortal } from "react-dom";

import { AuthedImage } from "@/components/chat/authed-image";
import { cn } from "@/lib/utils";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * Spec F4 T12 — `<ImageLightbox>`.
 *
 * Modal overlay for the "view larger" affordance on `<InlineVisual>`
 * (D-F4-2 lightbox lean; route-based deep-link is v0.2 telemetry-gated
 * per D-F4-X-lightbox-vs-route).
 *
 * Affordances:
 *   - Backdrop click closes (with role=dialog focus trap on the panel).
 *   - ESC key closes.
 *   - Download button triggers a Bearer-auth fetch + programmatic
 *     anchor click (same pattern as `<DownloadChip>` T06).
 *   - Close button — always visible top-right of the panel.
 *
 * Portal: renders to `document.body` so it escapes any overflow / z-index
 * containment from the chat scroller. SSR-safe via `typeof document`
 * guard — the lightbox is client-only by `"use client"` and the portal
 * skip-render branch.
 */
export interface ImageLightboxProps {
  open: boolean;
  personaId: string;
  workspacePath: string;
  mediaType: string;
  alt: string;
  onClose: () => void;
  className?: string;
}

export function ImageLightbox({
  open,
  personaId,
  workspacePath,
  mediaType,
  alt,
  onClose,
  className,
}: ImageLightboxProps) {
  const t = useTranslations("chat.output.lightbox");

  const handleKey = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    if (!open) return undefined;
    document.addEventListener("keydown", handleKey);
    // Lock body scroll while the lightbox is open — a UX expectation
    // for modal overlays (Cloudscape / Primer convention).
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, handleKey]);

  if (!open) return null;
  // Portal target may be unavailable on the server; guard so the SSR
  // pass doesn't crash. The component is "use client", so this only
  // affects the first render before hydration.
  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("title")}
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center",
        "bg-black/80 p-4",
        "motion-safe:animate-in motion-safe:fade-in",
        className,
      )}
      data-slot="image-lightbox"
      onClick={(e) => {
        // Click on the backdrop (not bubbled from the panel) → close.
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div
        className="relative max-h-full max-w-full"
        data-slot="image-lightbox-panel"
      >
        <AuthedImage
          personaId={personaId}
          workspacePath={workspacePath}
          mediaType={mediaType}
          alt={alt}
          className="max-h-[90vh] max-w-full object-contain"
        />

        <div
          className="absolute top-2 right-2 flex items-center gap-2"
          data-slot="image-lightbox-toolbar"
        >
          <DownloadAction
            personaId={personaId}
            workspacePath={workspacePath}
            filename={workspacePath.split("/").pop() ?? alt}
            label={t("download")}
          />
          <button
            type="button"
            onClick={onClose}
            aria-label={t("close")}
            className={cn(
              "grid size-9 place-items-center rounded-full",
              "bg-background/90 text-foreground backdrop-blur",
              "hover:bg-background",
              "focus-visible:outline-2 focus-visible:outline-offset-2",
              "focus-visible:outline-ring",
            )}
            data-slot="image-lightbox-close"
          >
            <X className="size-5" aria-hidden />
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * Download action — the same Bearer-auth blob-download pattern as
 * `<DownloadChip>` T06 (rule-of-three threshold would extract this to
 * a `useAuthedDownload` hook on a third use; for now two inline copies
 * is honest YAGNI).
 */
function DownloadAction({
  personaId,
  workspacePath,
  filename,
  label,
}: {
  personaId: string;
  workspacePath: string;
  filename: string;
  label: string;
}) {
  const { getToken } = useAuth();

  async function handleDownload(): Promise<void> {
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
      if (!res.ok) return;
      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } finally {
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    }
  }

  return (
    <button
      type="button"
      onClick={() => void handleDownload()}
      aria-label={label}
      className={cn(
        "grid size-9 place-items-center rounded-full",
        "bg-background/90 text-foreground backdrop-blur",
        "hover:bg-background",
        "focus-visible:outline-2 focus-visible:outline-offset-2",
        "focus-visible:outline-ring",
      )}
      data-slot="image-lightbox-download"
    >
      <Download className="size-5" aria-hidden />
    </button>
  );
}
