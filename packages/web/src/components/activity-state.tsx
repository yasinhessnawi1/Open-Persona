import type { ActivityView } from "@/lib/activity";

/**
 * P2 (T5) — the live "using <X>…" state, generalising Spec 35's `RecallState` (the
 * named-store "Recalling from <store>…" moment) to every capability the persona uses.
 *
 * Collapse-to-current (P2-D-5): renders ONLY the most recent in-flight activity — a burst
 * of rapid tool calls shows the latest, not a stack. A resolved activity clears (returns
 * `null`); the tool card / result surfaces the outcome (the activity channel is the live
 * state, SEPARATE from the card — P2-D-3 no-double-render).
 *
 * Identity-tinted: the dot uses the persona's `--v-id` colour (the persona working), set
 * by `personaIdentityStyle` on the surrounding turn; the human `label` carries the kind
 * ("Searching the web", "Running code"). The pulse is auto-silenced under
 * `prefers-reduced-motion` (the F1 universal override + the explicit `.v-activity-dot`
 * rule). ARIA: the caption is a polite live region so the state is announced; the dot is
 * decorative. Theme-agnostic (the tokens are theme-aware).
 */
export function ActivityState({ activities }: { activities?: ActivityView[] }) {
  const current =
    activities && activities.length > 0
      ? activities[activities.length - 1]
      : undefined;
  if (!current) return null;
  // Live only while in-flight; ok/error/denied clear (the card/result shows the outcome).
  const awaiting = current.status === "awaiting_approval";
  if (current.status !== "running" && !awaiting) return null;
  return (
    <div className="v-think__line" data-slot="activity-state">
      <span
        className="v-activity-dot"
        data-kind={current.kind}
        data-awaiting={awaiting ? "true" : undefined}
        aria-hidden="true"
      />
      {/* <output> carries an implicit role="status" + polite live region — the
          semantic element the a11y lint prefers over an ARIA role on a span. */}
      <output className="type-caption font-mono normal-case text-muted-foreground">
        {awaiting ? current.label : `${current.label}…`}
      </output>
    </div>
  );
}
