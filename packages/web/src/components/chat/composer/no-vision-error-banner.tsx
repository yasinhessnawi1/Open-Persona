"use client";

import { useTranslations } from "next-intl";
import { ErrorState } from "@/components/patterns/error-state";
import type { ApiError } from "@/lib/api/client";

/**
 * F3 (T15) — at-send fail-loud safety net for the NoVision case
 * (D-F3-X-no-vision-surface-shape (c)).
 *
 * Path (a) (pre-send disable) handles 99% of the surface: the attach
 * button is disabled when `capabilities.vision === false`. Path (c)
 * exists for the rare race where capabilities changed between page-
 * load and send — the user attached an image while vision was on,
 * then the deployment swapped to a text-only frontier. The send fails
 * with a structured 422 / 400 from the API; this banner surfaces it
 * via F2's `<ErrorState>`.
 *
 * Detection: the Spec 13 router raises `NoVisionTierConfiguredError`
 * which surfaces on the wire as a structured error body with
 * `error: "no_vision_tier"` (or `context.reason: "no_vision_tier"`).
 * We pattern-match on either shape.
 */
export interface NoVisionErrorBannerProps {
  /** The send-time error, if any. */
  error: ApiError | null;
  /** Dismiss the banner (typically called when the user removes the failed image). */
  onDismiss?: () => void;
}

export function isNoVisionError(err: ApiError | null): boolean {
  if (!err) return false;
  if (err.code === "no_vision_tier") return true;
  if (err.context?.reason === "no_vision_tier") return true;
  return false;
}

export function NoVisionErrorBanner({
  error,
  onDismiss,
}: NoVisionErrorBannerProps) {
  const t = useTranslations("chat.composer");

  if (!isNoVisionError(error)) return null;

  return (
    <ErrorState
      status="default"
      copy={{
        title: t("attach.imageDisabled"),
        detail: typeof error?.detail === "string" ? error.detail : undefined,
        action: onDismiss ? (
          <button
            type="button"
            onClick={onDismiss}
            className="type-ui text-primary underline-offset-4 hover:underline"
          >
            {t("attach.remove")}
          </button>
        ) : undefined,
      }}
    />
  );
}
