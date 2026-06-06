/**
 * Spec F2 T16 — TierBadge tests.
 *
 * Verifies:
 *   1. Three tiers (small / mid / frontier) render distinct token classes.
 *   2. `.type-caption` typography class is applied (closes the text-[0.65rem]
 *      magic-number violation surfaced in the F1 closeout).
 *   3. Unknown tiers fall back to a muted-foreground/border default.
 *   4. The data-tier attribute carries the tier name (testable hook).
 *
 * The settings toggle (`TIER_BADGE_SETTING`) hide behaviour is verified in
 * the existing settings test; this file focuses on the rendering contract.
 */

import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import { TierBadge } from "./tier-badge";

const messages = {
  chat: {
    tierLabel: "{tier} tier",
  },
};

function renderTier(tier: string) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <TierBadge tier={tier} />
    </NextIntlClientProvider>,
  );
}

describe("TierBadge", () => {
  it("renders the tier label visibly", () => {
    const { getByText } = renderTier("frontier");
    expect(getByText("frontier")).not.toBeNull();
  });

  it("applies .type-caption (closes the text-[0.65rem] magic; F2 T16)", () => {
    const { container } = renderTier("frontier");
    const badge = container.querySelector('[data-slot="tier-badge"]');
    expect(badge?.className).toContain("type-caption");
    // The legacy magic-number must be gone.
    expect(badge?.className).not.toContain("text-[0.65rem]");
  });

  it("renders each tier with its cool→hot token class", () => {
    const small = renderTier("small").container.querySelector(
      '[data-slot="tier-badge"]',
    );
    const mid = renderTier("mid").container.querySelector(
      '[data-slot="tier-badge"]',
    );
    const frontier = renderTier("frontier").container.querySelector(
      '[data-slot="tier-badge"]',
    );
    expect(small?.className).toContain("text-tier-small");
    expect(mid?.className).toContain("text-tier-mid");
    expect(frontier?.className).toContain("text-tier-frontier");
  });

  it("falls back to muted styling for unknown tiers", () => {
    const { container } = renderTier("unknown");
    const badge = container.querySelector('[data-slot="tier-badge"]');
    expect(badge?.className).toContain("text-muted-foreground");
    expect(badge?.className).toContain("border-border");
  });

  it("carries the tier name on data-tier for downstream querying", () => {
    const { container } = renderTier("mid");
    const badge = container.querySelector('[data-slot="tier-badge"]');
    expect(badge?.getAttribute("data-tier")).toBe("mid");
  });
});
