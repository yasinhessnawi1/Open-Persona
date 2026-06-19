"use client";

import { useTranslations } from "next-intl";
import { AuthedImage } from "@/components/chat/authed-image";
import { cn } from "@/lib/utils";

/**
 * Spec F4 T05 — `<InlineVisual>`.
 *
 * ONE component for BOTH Spec 15 generated images and Spec 17 inline
 * charts (R-F4-4 lock; rationale in
 * `docs/specs/phase2/spec_F4/decisions.md` §D-F4-4). The byte loading
 * is IDENTICAL — both reuse F3's `<AuthedImage>` + `useAuthedImageBlobUrl`
 * verbatim. Intent flows as a prop, not as a routing decision.
 *
 * Intent discriminator drives the caption surface:
 *   - `intent="image"`: standalone presentation; `caption` renders
 *     beneath the image when present.
 *   - `intent="chart"`: paired with the persona's analytical finding;
 *     `prose_context` renders beneath the chart as a `<figcaption>`.
 *
 * Default sizing per D-F4-2: max-width 480px inline (responsive down to
 * the parent's width on mobile). When `onViewLarger` is provided, the
 * image becomes a button that opens the lightbox (T12 wires the handler
 * via T10/T11); without it, the image is non-interactive.
 *
 * Composes the F3-shipped `<AuthedImage>` whose loading / 404 / 5xx
 * affordances are already production-tested (D-F3-X-image-serve-auth).
 * F4 doesn't reinvent the byte-loading surface — it carries intent as
 * data.
 */
export interface InlineVisualProps {
  personaId: string;
  workspacePath: string;
  mediaType: string;
  intent: "image" | "chart";
  alt: string;
  /** Caption rendered beneath when `intent="image"`. */
  caption?: string;
  /** Analytical-finding prose rendered beneath when `intent="chart"`. */
  prose_context?: string;
  /** "View larger" handler — wired by T10/T11 to open `<ImageLightbox>` (T12). */
  onViewLarger?: () => void;
  className?: string;
}

export function InlineVisual({
  personaId,
  workspacePath,
  mediaType,
  intent,
  alt,
  caption,
  prose_context,
  onViewLarger,
  className,
}: InlineVisualProps) {
  const t = useTranslations("chat.output.inlineVisual");
  // Caption surface is intent-driven; both render through <figcaption>
  // for semantics (and so the visual is announced as one figure to
  // assistive tech).
  const subtext = intent === "chart" ? prose_context : caption;

  // The AuthedImage carries the alt text and the loading/error surface.
  // Spec 35: bound the visual on BOTH axes and frame it so it reads as a neat
  // inline thumbnail rather than a column-dominating block — `object-contain`
  // keeps the aspect ratio inside the height cap; the border + radius give it
  // an intentional card edge (was an unframed, up-to-480px-wide image).
  const visual = (
    <AuthedImage
      personaId={personaId}
      workspacePath={workspacePath}
      mediaType={mediaType}
      alt={alt}
      className="h-auto max-h-80 w-auto max-w-full rounded-md border border-border object-contain"
    />
  );

  return (
    <figure
      className={cn("flex w-fit max-w-sm flex-col gap-1.5", className)}
      data-slot="inline-visual"
      data-intent={intent}
    >
      {onViewLarger !== undefined ? (
        <button
          type="button"
          onClick={onViewLarger}
          aria-label={t("viewLarger", { alt })}
          className={cn(
            "block cursor-zoom-in rounded-md",
            "focus-visible:outline-2 focus-visible:outline-offset-2",
            "focus-visible:outline-ring",
          )}
          data-slot="inline-visual-trigger"
        >
          {visual}
        </button>
      ) : (
        visual
      )}
      {subtext !== undefined && subtext.length > 0 ? (
        <figcaption
          className="type-caption italic text-muted-foreground"
          data-slot="inline-visual-caption"
        >
          {subtext}
        </figcaption>
      ) : null}
    </figure>
  );
}
