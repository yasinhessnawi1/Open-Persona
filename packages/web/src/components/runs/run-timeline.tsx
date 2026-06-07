"use client";

import { useTranslations } from "next-intl";
import type { RunView } from "@/lib/run";
import { StepCard } from "./step-card";

/**
 * Spec F2 T30 — RunTimeline (retokenised).
 *
 * Vertical timeline of `<StepCard>`s with a left-rail connector + a running
 * tail indicator while the agent is working between steps. Behaviour
 * preserved verbatim (per audit.md §runs.plumbing); presentation closed:
 * `text-sm` → `.type-ui` for the "working" / "no steps" labels.
 */
export function RunTimeline({
  view,
  onAnswer,
  personaId,
}: {
  view: RunView;
  onAnswer: (answer: string) => Promise<void>;
  /** F4 T11: drilled down from RunView → StepCard for the byte-load auth. */
  personaId: string;
}) {
  const t = useTranslations("runs");
  const running = view.status === "running";
  const awaitingStep = running
    ? view.steps.find((s) => s.question && !s.answered)?.step
    : undefined;

  if (view.steps.length === 0 && !running) {
    return (
      <p
        className="type-ui text-muted-foreground"
        data-slot="run-timeline-empty"
      >
        {t("noSteps")}
      </p>
    );
  }

  return (
    <div className="relative" data-slot="run-timeline">
      {view.steps.length > 0 ? (
        <span
          aria-hidden="true"
          className="absolute top-3 bottom-3 left-[14px] w-px bg-border"
        />
      ) : null}
      <ol className="flex flex-col gap-3">
        {view.steps.map((s) => (
          <StepCard
            key={s.step}
            step={s}
            awaiting={s.step === awaitingStep}
            onAnswer={onAnswer}
            personaId={personaId}
          />
        ))}
      </ol>
      {running && awaitingStep === undefined ? (
        <div
          className="type-ui mt-3 flex items-center gap-2 pl-8 text-muted-foreground"
          data-slot="run-timeline-working"
          aria-live="polite"
        >
          <span
            aria-hidden="true"
            className="size-2 animate-pulse rounded-full bg-primary"
          />
          {t("working")}
        </div>
      ) : null}
    </div>
  );
}
