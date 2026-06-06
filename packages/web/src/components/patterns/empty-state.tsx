/**
 * Spec F2 T22 — EmptyState pattern.
 *
 * F1's UI voice: empty invites, never apologises. The pattern: a centred
 * card with optional icon + Fraunces title (.type-heading) + body in
 * muted-foreground + optional primary action. Used wherever a list, table,
 * or fetched collection is empty.
 *
 * Server component (D-F2-3). All user-facing strings must come through
 * next-intl `t()` — the consumer passes them already-translated; this
 * primitive is i18n-agnostic by design.
 */

import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  /** Optional icon — rendered above the title, in muted-foreground. */
  icon?: ReactNode;
  /** Required short headline (already-translated). */
  title: ReactNode;
  /** Body copy — inviting tone (already-translated). */
  description?: ReactNode;
  /** Optional primary action — a <Link> or <button>. */
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <Card
      className={cn(
        "flex flex-col items-center gap-3 border-dashed py-20 text-center",
        className,
      )}
      data-slot="empty-state"
    >
      {icon ? (
        <div
          className="text-muted-foreground"
          aria-hidden="true"
          data-slot="empty-state-icon"
        >
          {icon}
        </div>
      ) : null}
      <h2 className="type-heading max-w-sm" data-slot="empty-state-title">
        {title}
      </h2>
      {description ? (
        <p
          className="type-ui max-w-sm text-muted-foreground"
          data-slot="empty-state-description"
        >
          {description}
        </p>
      ) : null}
      {action ? (
        <div className="mt-2" data-slot="empty-state-action">
          {action}
        </div>
      ) : null}
    </Card>
  );
}
