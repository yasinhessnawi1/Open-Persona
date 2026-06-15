"use client";

import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

/** Shared loading / unavailable / error affordances for the panel renderers. */

export function RendererLoading({ className }: { className?: string }) {
  const t = useTranslations("chat.output.renderer");
  return (
    <output
      className={cn("block p-4 text-sm text-muted-foreground", className)}
      data-slot="renderer-loading"
    >
      {t("loading")}
    </output>
  );
}

export function RendererUnavailable({ className }: { className?: string }) {
  const t = useTranslations("chat.output.renderer");
  return (
    <div
      className={cn("p-4 text-sm text-muted-foreground", className)}
      data-slot="renderer-unavailable"
    >
      {t("unavailable")}
    </div>
  );
}

export function RendererError({
  reason,
  className,
}: {
  reason: string;
  className?: string;
}) {
  const t = useTranslations("chat.output.renderer");
  return (
    <div
      className={cn("p-4 text-sm text-destructive", className)}
      role="alert"
      data-slot="renderer-error"
    >
      {t("error", { reason })}
    </div>
  );
}

/** Plain `<pre>` source view — the panel's RAW mode for every text format. */
export function RawSource({ text }: { text: string }) {
  return (
    <pre
      className="overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs leading-relaxed"
      data-slot="renderer-raw"
    >
      {text}
    </pre>
  );
}
