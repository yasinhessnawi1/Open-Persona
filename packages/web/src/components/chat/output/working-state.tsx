"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

/**
 * Spec F4 T08 — `<WorkingState>`.
 *
 * Shared component consumed by BOTH `message-element.tsx` (T10) and
 * `step-card.tsx` (T11) per D-F4-5. Single source of truth for the
 * "operation in flight" affordance.
 *
 * Visual reuses the F1 motion pattern verbatim from
 * `message-element.tsx:504-528` ToolRunningIndicator — three
 * `size-2 bg-muted-foreground/70` dots with `animate-pulse` and a
 * staggered animationDelay (0/200/400 ms). `<output>` element carries
 * implicit `role="status"` (Biome `useSemanticElements` convention) so
 * the label is announced politely to screen readers.
 *
 * Closed `operation` enum mirrors `OutputContent.WorkingOutput.operation`
 * (D-F4-X-renderer-normaliser-shape). Each operation maps to its own
 * i18n key — never bare "Loading…" (the dominant UX anti-pattern per
 * R-F4-3 working-state survey). `label` overrides the default copy
 * when the caller has a more specific verb (e.g. tool name from
 * `tool_calling` event).
 *
 * Reduced-motion: `motion-reduce:[&_span]:animate-none` silences the
 * pulse for users with `prefers-reduced-motion: reduce`. Dots remain
 * visible as a static three-dot row; the label still carries meaning.
 *
 * F4-local under `packages/web/src/components/chat/output/` per
 * D-F4-X-working-state-shared-component; promotes to F2 when a second
 * consumer surfaces (likely F5 long-running operations).
 */
export interface WorkingStateProps {
  /** Closed F4 capability set (matches `OutputContent.WorkingOutput.operation`). */
  operation: "image_gen" | "code_exec" | "doc_gen";
  /** Optional override label (e.g. tool name) — falls back to the operation default. */
  label?: string;
  className?: string;
}

function defaultLabelKey(operation: WorkingStateProps["operation"]): string {
  switch (operation) {
    case "image_gen":
      return "imageGen";
    case "code_exec":
      return "codeExec";
    case "doc_gen":
      return "docGen";
  }
}

export function WorkingState({
  operation,
  label,
  className,
}: WorkingStateProps) {
  const t = useTranslations("chat.output.workingState");
  const visibleLabel = label ?? t(defaultLabelKey(operation));

  return (
    <output
      aria-label={visibleLabel}
      className={cn(
        "type-ui inline-flex items-center gap-2 py-1.5 italic text-muted-foreground",
        className,
      )}
      data-slot="working-state"
      data-operation={operation}
    >
      <span aria-hidden="true" className="inline-flex items-center gap-1">
        <span
          className={cn(
            "size-2 animate-pulse rounded-full bg-muted-foreground/70",
            "motion-reduce:animate-none",
          )}
          style={{ animationDelay: "0ms" }}
        />
        <span
          className={cn(
            "size-2 animate-pulse rounded-full bg-muted-foreground/70",
            "motion-reduce:animate-none",
          )}
          style={{ animationDelay: "200ms" }}
        />
        <span
          className={cn(
            "size-2 animate-pulse rounded-full bg-muted-foreground/70",
            "motion-reduce:animate-none",
          )}
          style={{ animationDelay: "400ms" }}
        />
      </span>
      <span>{visibleLabel}</span>
    </output>
  );
}
