/**
 * Spec F2 T22 — ErrorState pattern.
 *
 * D-F2-9 locked: one template + per-status copy overrides for the structured
 * API error shapes Spec-08 + Spec-11 ship:
 *   - default     `{error, detail?}` — generic 5xx / unspecified
 *   - 422         Pydantic field-level detail (preserved per D-11-14)
 *   - 429         rate-limit (surfaces `Retry-After` if present)
 *   - 402         credit-exhausted (Spec-11 zero-guard + "free credits /
 *                 contact support" copy)
 *
 * Server component (D-F2-3). The consumer passes already-translated strings
 * via the `status` prop discriminator + per-status copy entries. This
 * primitive is i18n-agnostic.
 *
 * F1 UI voice: honest + human; never apologetic. Errors explain what went
 * wrong + name a path forward, with the inviting-not-blaming tone the
 * empty-state pattern shares.
 */

import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * The four supported error statuses + a generic fallback. New statuses
 * (e.g., a future 451 Unavailable-For-Legal-Reasons) extend this union.
 */
export type ErrorStatus = "default" | 422 | 429 | 402;

export interface ErrorStateCopy {
  /** Short headline (already-translated). */
  title: ReactNode;
  /** Body copy explaining the situation (already-translated). */
  description?: ReactNode;
  /** Optional structured detail (e.g., 422 field-level errors). */
  detail?: ReactNode;
  /** Optional retry / contact action. */
  action?: ReactNode;
}

interface ErrorStateProps {
  status: ErrorStatus;
  copy: ErrorStateCopy;
  className?: string;
}

const STATUS_TONE: Record<ErrorStatus, { ring: string; iconLabel: string }> = {
  default: { ring: "ring-destructive/30", iconLabel: "Error" },
  422: { ring: "ring-destructive/30", iconLabel: "Validation error" },
  429: { ring: "ring-tier-mid/40", iconLabel: "Rate limited" },
  402: { ring: "ring-primary/40", iconLabel: "Credits exhausted" },
};

export function ErrorState({ status, copy, className }: ErrorStateProps) {
  const tone = STATUS_TONE[status];
  return (
    <Card
      className={cn("flex flex-col gap-3 p-6 ring-1", tone.ring, className)}
      data-slot="error-state"
      data-status={String(status)}
    >
      <h2 className="type-heading" data-slot="error-state-title">
        {copy.title}
      </h2>
      {copy.description ? (
        <p
          className="type-body text-muted-foreground"
          data-slot="error-state-description"
        >
          {copy.description}
        </p>
      ) : null}
      {copy.detail ? (
        <div
          className="type-ui rounded-md border bg-muted/40 p-3 text-muted-foreground"
          data-slot="error-state-detail"
        >
          {copy.detail}
        </div>
      ) : null}
      {copy.action ? (
        <div className="mt-2" data-slot="error-state-action">
          {copy.action}
        </div>
      ) : null}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Adapter helpers: normalise the API error shapes into ErrorStateCopy. The
// consumer passes the raw ApiError / response body; these helpers extract
// the relevant fields + leave the user-facing strings to the caller's i18n
// `t()` keys.

/**
 * Adapt a 422 Pydantic field-level detail array (Spec-08 + D-11-14
 * preserved). Returns an unordered list of "<field>: <message>" entries
 * the consumer can render via the `detail` slot.
 */
export function pydantic422Detail(
  detail: Array<{ loc?: readonly (string | number)[]; msg: string }>,
): ReactNode {
  return (
    <ul className="list-disc pl-5">
      {detail.map((d, i) => (
        <li
          // biome-ignore lint/suspicious/noArrayIndexKey: error detail entries are positional
          key={i}
        >
          {d.loc && d.loc.length > 0 ? (
            <span className="font-mono">{d.loc.join(".")}: </span>
          ) : null}
          {d.msg}
        </li>
      ))}
    </ul>
  );
}
