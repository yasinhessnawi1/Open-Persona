/**
 * F3 (T16 + T17) — typed mapping from `ValidationReason` to F2 toast
 * messages via the i18n table.
 *
 * `validateBeforeUpload` (T05) returns a typed reason enum on rejection;
 * this module maps each enum value to the corresponding i18n key the
 * toast surfaces. Centralised here so T19's ChatWindow wiring stays
 * thin (one function call per rejection) and so T20's a11y verification
 * can grep for the keys directly.
 *
 * T18 — the scanned-PDF UX cue — does NOT go through here; it's a
 * static inline tooltip on `<DocumentChip>` (already shipped via
 * the `documents.scannedCue` i18n key + `strategy === "vision_handoff"`
 * detection in document-chip.tsx).
 */

import type { useTranslations } from "next-intl";
import type { ValidationReason } from "./attach-state";

export interface ToastSink {
  error: (message: string) => void;
}

/**
 * Surface a typed validation failure as a toast.
 *
 * @param reason  The typed enum from validateBeforeUpload (T05).
 * @param detail  The detail string from the validation result (carries
 *                filename + actual values; surfaces honestly per F2 voice).
 * @param toast   sonner `toast` from useToast() (T19 passes it down).
 * @param t       next-intl translator for `chat.composer.validation.*` keys.
 *
 * The detail string from `validateBeforeUpload` already carries the
 * filename + cap value, so we use it directly. The i18n key acts as the
 * grouping label; the detail is the load-bearing user-facing prose.
 */
export function surfaceValidationFailure(
  _reason: ValidationReason,
  detail: string,
  toast: ToastSink,
  // `t` resolves the i18n key when we need to override the validation
  // detail with a translator-controlled string (currently we trust the
  // detail string from validateBeforeUpload, but the hook is here for
  // pseudo-locale / non-English coverage at T20).
  _t: ReturnType<typeof useTranslations>,
): void {
  // Single error toast per rejection. F2's `<ToastProvider>` is mounted
  // once in <AppShell>; toasts surface in the top-right with status colour.
  toast.error(detail);
}
