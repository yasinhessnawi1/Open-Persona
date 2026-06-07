"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";
import { lazy, Suspense, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Spec F4 T07 — `<ResultBlock>`.
 *
 * Surfaces code-execution stdout (Spec 12) as a legible monospace block
 * with:
 *   - Truncation indicator + "show full" affordance when stdout exceeds
 *     {@link STDOUT_TRUNCATE_LINES} lines (D-F4-3 monospace-with-light-
 *     structure for v1; tabular auto-detection is v0.2 telemetry-gated).
 *   - Optional collapsible code-above-result for F1 instrument-
 *     transparency (D-F4-1 collapsed by default). Code rendered via
 *     Shiki, lazy-loaded behind `React.lazy + Suspense` so the
 *     highlighter chunk ships only when the user expands the code
 *     (D-F4-X-instrument-transparency-affordance; R-F4-X-syntax-
 *     highlighting bundle budget).
 *
 * F4-local under `packages/web/src/components/chat/output/` per
 * D-F4-X-result-block-placement; promotes to F2 on F5 second-consumer
 * reuse (strangler-fig mirror of D-F3-X-chip-placement).
 */

/** Stdout exceeding this many lines triggers truncation + show-full. */
const STDOUT_TRUNCATE_LINES = 12;

const HighlightedCode = lazy(() => import("./highlighted-code"));

export interface ResultBlockProps {
  /** ToolResult.content — the rendered stdout (+ outcome / files sections). */
  stdout: string;
  /** Producer-reported truncation from the runtime / sandbox cap. */
  truncated?: boolean;
  /** Optional code that produced this result (F1 instrument-transparency). */
  code?: string;
  /** `python` / `bash` / etc — informs the Shiki highlighter language pick. */
  language?: string;
  className?: string;
}

export function ResultBlock({
  stdout,
  truncated = false,
  code,
  language = "python",
  className,
}: ResultBlockProps) {
  const t = useTranslations("chat.output.resultBlock");
  // D-F4-1: collapsed by default. Expand chevron in the header.
  const [codeExpanded, setCodeExpanded] = useState(false);
  const [stdoutExpanded, setStdoutExpanded] = useState(false);

  const lines = stdout.split("\n");
  const shouldTruncate = lines.length > STDOUT_TRUNCATE_LINES;
  const visibleLines =
    shouldTruncate && !stdoutExpanded
      ? lines.slice(0, STDOUT_TRUNCATE_LINES)
      : lines;
  const hiddenCount =
    shouldTruncate && !stdoutExpanded
      ? lines.length - STDOUT_TRUNCATE_LINES
      : 0;

  const hasCode = code !== undefined && code.length > 0;

  return (
    <div
      className={cn("overflow-hidden rounded-md border bg-muted/30", className)}
      data-slot="result-block"
    >
      {hasCode ? (
        <div className="border-b" data-slot="result-block-code-section">
          <button
            type="button"
            onClick={() => setCodeExpanded((v) => !v)}
            aria-expanded={codeExpanded}
            className={cn(
              "type-caption flex w-full items-center gap-1 px-3 py-1.5",
              "font-mono text-muted-foreground hover:bg-muted/50",
              "focus-visible:outline-2 focus-visible:outline-offset-[-2px]",
              "focus-visible:outline-ring",
            )}
            data-slot="result-block-code-toggle"
          >
            {codeExpanded ? (
              <ChevronDown className="size-3.5" aria-hidden />
            ) : (
              <ChevronRight className="size-3.5" aria-hidden />
            )}
            {codeExpanded ? t("hideCode") : t("showCode")}
          </button>
          {codeExpanded ? (
            <Suspense
              fallback={
                <pre
                  className="overflow-x-auto p-3 font-mono text-sm leading-relaxed"
                  data-slot="result-block-code-fallback"
                >
                  {code}
                </pre>
              }
            >
              <HighlightedCode code={code} lang={language} />
            </Suspense>
          ) : null}
        </div>
      ) : null}

      <pre
        className="overflow-x-auto p-3 font-mono text-sm leading-[1.5]"
        data-slot="result-block-stdout"
      >
        {visibleLines.join("\n")}
      </pre>

      {truncated ? (
        <div
          className="type-caption border-t px-3 py-1 italic text-muted-foreground"
          data-slot="result-block-upstream-truncated"
        >
          {t("upstreamTruncated")}
        </div>
      ) : null}

      {shouldTruncate ? (
        <div className="flex justify-center border-t px-3 py-1.5">
          <button
            type="button"
            onClick={() => setStdoutExpanded((v) => !v)}
            className={cn(
              "type-caption text-muted-foreground hover:text-foreground",
              "focus-visible:outline-2 focus-visible:outline-offset-2",
              "focus-visible:outline-ring",
            )}
            data-slot="result-block-stdout-toggle"
          >
            {stdoutExpanded
              ? t("showLess")
              : t("showFull", { count: hiddenCount })}
          </button>
        </div>
      ) : null}
    </div>
  );
}
