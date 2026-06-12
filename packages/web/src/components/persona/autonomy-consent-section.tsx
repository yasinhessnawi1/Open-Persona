"use client";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * AutonomyConsentSection — spec 21 T11 (D-21-1/2).
 *
 * Two persona-settings controls:
 *  - the autonomy selector (cautious / balanced / decisive, spec §2.1) — tunes
 *    how readily the persona asks proactive questions and auto-dispatches;
 *  - the auto-dispatch consent toggle (D-21-2: toggle + inline warning, never a
 *    modal). On = consent granted; toggling off revokes back to "ask" (NULL),
 *    which re-arms the consent prompt on the next autonomous task (D-21-17).
 *
 * Presentational/controlled: the parent owns persistence (autonomy via the
 * persona YAML PATCH; consent via PATCH /v1/personas/:id/consent).
 */
export type AutonomyLevel = "cautious" | "balanced" | "decisive";

const AUTONOMY_OPTIONS: {
  level: AutonomyLevel;
  title: string;
  description: string;
}[] = [
  {
    level: "cautious",
    title: "Cautious",
    description: "Asks frequently — checks in on any ambiguity before acting.",
  },
  {
    level: "balanced",
    title: "Balanced",
    description:
      "Asks on structural ambiguity; assumes reasonable defaults on minor details.",
  },
  {
    level: "decisive",
    title: "Decisive",
    description:
      "Asks rarely — only on safety-critical gaps; otherwise proceeds.",
  },
];

export function AutonomyConsentSection({
  autonomy,
  onAutonomyChange,
  consent,
  onConsentChange,
  pending = false,
}: {
  autonomy: AutonomyLevel;
  onAutonomyChange: (level: AutonomyLevel) => void;
  /** Tri-state: true = granted, false = declined, null = never asked / revoked. */
  consent: boolean | null;
  onConsentChange: (granted: boolean | null) => void;
  pending?: boolean;
}) {
  const granted = consent === true;

  return (
    <section
      className="flex flex-col gap-6"
      data-slot="autonomy-consent-section"
    >
      <div className="flex flex-col gap-2" data-slot="autonomy-selector">
        <h3 className="type-body font-medium">Autonomy</h3>
        <p className="type-caption text-muted-foreground">
          How readily this persona asks clarifying questions and acts on its
          own.
        </p>
        <div className="mt-1 flex flex-col gap-1.5">
          {AUTONOMY_OPTIONS.map((opt) => (
            <button
              key={opt.level}
              type="button"
              disabled={pending}
              aria-pressed={autonomy === opt.level}
              onClick={() => onAutonomyChange(opt.level)}
              className={cn(
                "flex flex-col items-start gap-0.5 rounded-md border p-3 text-left transition-colors",
                autonomy === opt.level
                  ? "border-primary bg-primary/5"
                  : "border-border hover:bg-muted/50",
              )}
              data-slot="autonomy-option"
            >
              <span className="type-body font-medium">{opt.title}</span>
              <span className="type-caption text-muted-foreground">
                {opt.description}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-2" data-slot="consent-toggle">
        <div className="flex items-center justify-between gap-3">
          <div className="flex flex-col">
            <h3 className="type-body font-medium">Automatic tasks</h3>
            <p className="type-caption text-muted-foreground">
              Let this persona start tasks on your behalf without asking each
              time.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={granted}
            disabled={pending}
            onClick={() => onConsentChange(granted ? null : true)}
            className={cn(
              buttonVariants({
                variant: granted ? "default" : "outline",
                size: "sm",
              }),
            )}
            data-slot="consent-switch"
          >
            {granted ? "On" : "Off"}
          </button>
        </div>
        {granted ? (
          <p
            className="type-caption rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-muted-foreground"
            data-slot="consent-warning"
          >
            Turning this off stops future automatic tasks only — tasks already
            running or completed are not affected. You'll be asked again the
            next time this persona wants to start a task. Every task appears in
            the activity log.
          </p>
        ) : null}
      </div>
    </section>
  );
}
