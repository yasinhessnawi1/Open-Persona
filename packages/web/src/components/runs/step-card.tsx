"use client";

import { useTranslations } from "next-intl";
import { ActivityState } from "@/components/activity-state";
import { OutputList } from "@/components/chat/output/dispatcher";
import { ToolCallCard } from "@/components/chat/tool-call-card";
import { Markdown } from "@/components/ui/markdown";
import type { RunStep } from "@/lib/run";
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
  last,
  onAnswer,
  personaId,
}: {
  step: RunStep;
  awaiting: boolean;
  /** Last step in the timeline — suppresses the trailing connector line. */
  last?: boolean;
  onAnswer: (answer: string) => Promise<void>;
  /**
   * F4 T11: passed through to the OutputList → OutputDispatcher so the
   * Bearer-auth byte loader (`useAuthedImageBlobUrl`) can resolve images
   * and the download chip can fetch with auth. Drilled down from
   * `RunView` → `RunTimeline` → `StepCard`; the run's `persona_id`
   * already lives on `RunStatusResponse`.
   */
  personaId: string;
}) {
  const t = useTranslations("runs");
  const isFinal = step.final !== undefined;
  const isError = step.error !== undefined;
  // Spec 35: the .v-run-step rail dot reads the run-progress state — a filled
  // identity dot for the final step, a pulsing identity ring while awaiting
  // input, a neutral dot otherwise (error surfaces in the body copy).
  const dotState = isFinal ? "done" : awaiting ? "running" : undefined;

  return (
    <li className="v-run-step" data-slot="step-card">
      <div className="v-run-rail">
        <span className="v-run-dot" data-state={dotState} aria-hidden="true" />
        {last ? null : <span className="v-run-line" aria-hidden="true" />}
      </div>

      <div
        className="v-run-step__body flex min-w-0 flex-col gap-2.5 pb-1"
        data-final={isFinal ? "true" : "false"}
        data-error={isError ? "true" : "false"}
      >
        <div className="v-run-step__head">
          <span className="v-run-step__type" data-slot="step-label">
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
          {awaiting ? (
            <span className="v-dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
          ) : null}
        </div>

        {/* P2 (T5): the live "using <X>…" state for an in-flight capability on this
            step — the same component the chat surface renders (one cross-surface
            affordance). Collapses to the current activity; clears on resolve. */}
        <ActivityState activities={step.activities} />

        {step.tools.length > 0 ? (
          <div className="flex flex-col gap-1.5" data-slot="step-tools">
            {step.tools.map((tool, i) => (
              <ToolCallCard key={`${tool.toolName}-${i}`} entry={tool} />
            ))}
          </div>
        ) : null}

        {/* F4 T11 (D-F4-X-step-card-output-extension): step.outputs are
            derived view-time by runViewFromEvents (T04) from existing
            tool_calling + tool_result events. The OutputList composes the
            SAME renderer set as the chat path (T10) — single source of
            truth for rich-output rendering across both surfaces. */}
        {step.outputs.length > 0 ? (
          <OutputList
            personaId={personaId}
            outputs={step.outputs}
            className="mt-1"
          />
        ) : null}

        {step.reasoning ? (
          <p
            className="v-run-step__detail whitespace-pre-wrap"
            data-slot="step-reasoning"
          >
            {step.reasoning}
          </p>
        ) : null}

        {step.question ? (
          <div className="flex flex-col gap-2">
            {awaiting ? (
              <AskUserPrompt
                question={step.question}
                options={step.options}
                allowFreeForm={step.allowFreeForm}
                onAnswer={onAnswer}
              />
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
      </div>
    </li>
  );
}
