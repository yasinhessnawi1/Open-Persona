"use client";

import { useTranslations } from "next-intl";
import type { BudgetSnapshot } from "@/lib/sse-types";
import { cn } from "@/lib/utils";

// The session soft-cap ramps weights toward cost from this fraction of the cap
// (routing_budget.py `_ramp` starts at 0.8). The UI surfaces the same knee — no
// invented threshold (D-31-2).
const SESSION_APPROACH_KNEE = 0.8;

function fmt(cents: number): string {
  return cents.toFixed(2);
}

/**
 * BudgetIndicator (Spec 31, D-31-2). Renders the per-session spend-vs-cap with
 * states tied 1:1 to what Spec 23 actually enforces:
 *  - per-session SOFT cap → a meter + an "approaching" note at the real 0.8 knee;
 *  - per-turn HARD cap → an informational line (a turn that can't fit fails loud
 *    before a `done`, so it never renders here — this is the configured value);
 *  - per-day cap → the configured-but-deferred fail-loud note (honest, not silent).
 *
 * Renders nothing when no snapshot is present (rule-based / no-cap turns).
 */
export function BudgetIndicator({ budget }: { budget?: BudgetSnapshot }) {
  const t = useTranslations("chat.budget");
  if (!budget) return null;

  const spent = budget.session_spent_cents;
  const sessionCap = budget.max_cents_per_session;
  const approaching =
    sessionCap !== undefined &&
    sessionCap > 0 &&
    spent / sessionCap >= SESSION_APPROACH_KNEE;

  return (
    <div
      className="type-caption flex flex-col gap-0.5 text-muted-foreground"
      data-slot="budget-indicator"
    >
      <span
        className={cn(approaching && "text-tier-mid")}
        data-slot="budget-session"
        data-approaching={approaching}
      >
        {sessionCap !== undefined
          ? t("session", { spent: fmt(spent), cap: fmt(sessionCap) })
          : t("sessionNoCap", { spent: fmt(spent) })}
      </span>
      {approaching ? (
        <span data-slot="budget-approaching">{t("approaching")}</span>
      ) : null}
      {budget.max_cents_per_turn !== undefined ? (
        <span data-slot="budget-perturn">
          {t("perTurnCap", { cap: fmt(budget.max_cents_per_turn) })}
        </span>
      ) : null}
      {budget.max_cents_per_day !== undefined ? (
        <span
          className="text-amber-600 dark:text-amber-500"
          data-slot="budget-perday-unenforced"
        >
          {t("perDayUnenforced")}
        </span>
      ) : null}
    </div>
  );
}
