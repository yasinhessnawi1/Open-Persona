"use client";

import { useTranslations } from "next-intl";
import { ToolCallCard } from "@/components/chat/tool-call-card";
import { Card } from "@/components/ui/card";
import { Markdown } from "@/components/ui/markdown";
import type { RunStep } from "@/lib/run";
import { cn } from "@/lib/utils";
import { AskUserPrompt } from "./ask-user-prompt";

/**
 * Spec F2 T30 — StepCard (retokenised).
 *
 * Per-step card in the run timeline. Behaviour preserved verbatim (per
 * audit.md §runs.plumbing): the tools list, ask-user-prompt branch, final
 * markdown, max-steps note, and error treatment all stay; the `step.tier`
 * value still drives the right-rail label.
 *
 * REPLACED (presentation only):
 *   - 2× `font-mono text-[0.65rem] tracking-wide uppercase` (step label +
 *     tier label, audit lines 47 + 51) → `.type-caption font-mono uppercase`;
 *   - reasoning `text-sm leading-relaxed` → `.type-body`;
 *   - question fallback `text-sm font-medium` → `.type-body font-medium`;
 *   - "answered" hint `text-xs` → `.type-caption`;
 *   - max-steps note `text-xs text-tier-mid` → `.type-caption text-tier-mid`;
 *   - max-steps body `text-sm` → `.type-body`;
 *   - error `text-sm text-destructive` → `.type-ui text-destructive`.
 */
export function StepCard({
  step,
  awaiting,
  onAnswer,
}: {
  step: RunStep;
  awaiting: boolean;
  onAnswer: (answer: string) => Promise<void>;
}) {
  const t = useTranslations("runs");
  const isFinal = step.final !== undefined;
  const isError = step.error !== undefined;

  return (
    <li className="relative pl-8" data-slot="step-card">
      {/* Timeline node. Vermilion while this step still awaits input. */}
      <span
        aria-hidden="true"
        className={cn(
          "absolute top-3 left-[9px] size-2.5 rounded-full ring-4 ring-background",
          isError
            ? "bg-destructive"
            : isFinal
              ? "bg-tier-small"
              : awaiting
                ? "animate-pulse bg-primary"
                : "bg-muted-foreground/40",
        )}
      />
      <Card
        className={cn(
          "gap-2.5 p-4",
          isFinal && "border-tier-small/30",
          isError && "border-destructive/30",
        )}
        data-final={isFinal ? "true" : "false"}
        data-error={isError ? "true" : "false"}
      >
        <div className="flex items-center justify-between gap-2">
          <span
            className="type-caption font-mono text-muted-foreground uppercase"
            data-slot="step-label"
          >
            {isFinal ? t("finalLabel") : t("stepLabel", { n: step.step + 1 })}
          </span>
          {step.tier ? (
            <span
              className="type-caption font-mono text-muted-foreground uppercase"
              data-slot="step-tier"
            >
              {step.tier}
            </span>
          ) : null}
        </div>

        {step.tools.length > 0 ? (
          <div className="flex flex-col gap-1.5" data-slot="step-tools">
            {step.tools.map((tool, i) => (
              <ToolCallCard key={`${tool.toolName}-${i}`} entry={tool} />
            ))}
          </div>
        ) : null}

        {step.reasoning ? (
          <p
            className="type-body whitespace-pre-wrap text-muted-foreground"
            data-slot="step-reasoning"
          >
            {step.reasoning}
          </p>
        ) : null}

        {step.question ? (
          <div className="flex flex-col gap-2">
            {awaiting ? (
              <AskUserPrompt question={step.question} onAnswer={onAnswer} />
            ) : (
              <div
                className="rounded-md border bg-muted/40 p-3"
                data-slot="step-question-resolved"
              >
                <p className="type-body font-medium">{step.question}</p>
                {step.answered ? (
                  <p className="type-caption mt-1 text-muted-foreground">
                    {t("answered")}
                  </p>
                ) : null}
              </div>
            )}
          </div>
        ) : null}

        {step.final !== undefined ? (
          <div data-slot="step-final">
            <Markdown>{step.final}</Markdown>
          </div>
        ) : null}

        {step.maxSteps !== undefined ? (
          <div data-slot="step-max-steps">
            <p className="type-caption mb-1 text-tier-mid">
              {t("maxStepsNote")}
            </p>
            <Markdown>{step.maxSteps}</Markdown>
          </div>
        ) : null}

        {step.error !== undefined ? (
          <p
            className="type-ui text-destructive"
            role="alert"
            data-slot="step-error"
          >
            {step.error}
          </p>
        ) : null}
      </Card>
    </li>
  );
}
