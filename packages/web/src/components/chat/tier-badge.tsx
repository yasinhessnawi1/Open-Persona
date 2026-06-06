"use client";

import { useTranslations } from "next-intl";
import { TIER_BADGE_SETTING, useBoolSetting } from "@/lib/hooks/use-setting";
import { cn } from "@/lib/utils";

// Tier tokens escalate cool→hot (small=slate · mid=amber · frontier=vermilion),
// making the routing layer tangible (spec §4.1). F1 T14 confirmed chroma —
// not lightness — carries the firepower signal.
const TIER_CLASS: Record<string, string> = {
  frontier: "border-tier-frontier/40 text-tier-frontier",
  mid: "border-tier-mid/50 text-tier-mid",
  small: "border-tier-small/50 text-tier-small",
};

export function TierBadge({ tier }: { tier: string }) {
  const t = useTranslations("chat");
  // Power-user setting: hide tier badges (settings toggle, persisted locally).
  const [visible] = useBoolSetting(TIER_BADGE_SETTING, true);
  if (!visible) return null;
  // F2 T16 retokenise: text-[0.65rem] magic → .type-caption (Geist Mono +
  // uppercase + tracking are part of the F1 type-scale utility class, so the
  // inline font-mono/tracking-wide/uppercase classes are redundant — removed).
  return (
    <span
      title={t("tierLabel", { tier })}
      className={cn(
        "type-caption inline-flex w-fit items-center rounded border px-1.5 py-0.5",
        TIER_CLASS[tier] ?? "border-border text-muted-foreground",
      )}
      data-slot="tier-badge"
      data-tier={tier}
    >
      {tier}
    </span>
  );
}
