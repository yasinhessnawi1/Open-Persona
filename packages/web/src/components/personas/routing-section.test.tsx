/**
 * Spec 31 T2 — <RoutingSection> tests (D-31-3 / D-31-2).
 *
 * Enable toggle → preset chips (cost/balanced/quality/speed) → advanced raw
 * weights (auto-opens on Custom) → budget cap inputs (blank ⇒ null) with the
 * per-day fail-loud warning.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import messages from "@/i18n/messages/en.json";
import {
  DEFAULT_SCORING_WEIGHTS,
  presetToWeights,
  type RoutingView,
} from "@/lib/persona-draft";
import { RoutingSection } from "./routing-section";

const OFF: RoutingView = {
  intelligentEnabled: false,
  weights: DEFAULT_SCORING_WEIGHTS,
  fallbackOnMiss: true,
  budget: {
    maxCentsPerTurn: null,
    maxCentsPerSession: null,
    maxCentsPerDay: null,
  },
};

function renderSection(value: RoutingView, onChange = vi.fn()) {
  const utils = render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <RoutingSection value={value} onChange={onChange} />
    </NextIntlClientProvider>,
  );
  return { ...utils, onChange };
}

describe("RoutingSection — enable toggle", () => {
  it("hides the config when intelligent routing is off", () => {
    const { container } = renderSection(OFF);
    expect(container.querySelector('[data-slot="routing-config"]')).toBeNull();
  });

  it("enabling calls onChange with intelligentEnabled true", () => {
    const { onChange } = renderSection(OFF);
    fireEvent.click(
      screen.getByRole("switch", { name: /intelligent routing/i }),
    );
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ intelligentEnabled: true }),
    );
  });

  it("shows the config when enabled", () => {
    const { container } = renderSection({ ...OFF, intelligentEnabled: true });
    expect(
      container.querySelector('[data-slot="routing-config"]'),
    ).not.toBeNull();
  });
});

describe("RoutingSection — presets (D-31-3)", () => {
  it("marks the matching preset active (balanced == default weights)", () => {
    const { container } = renderSection({ ...OFF, intelligentEnabled: true });
    const balanced = container.querySelector('[data-preset="balanced"]');
    expect(balanced?.getAttribute("data-active")).toBe("true");
  });

  it("selecting a preset emits its weight vector", () => {
    const { container, onChange } = renderSection({
      ...OFF,
      intelligentEnabled: true,
    });
    const costChip = container.querySelector(
      '[data-preset="cost"]',
    ) as HTMLElement;
    fireEvent.click(costChip);
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ weights: presetToWeights("cost") }),
    );
  });

  it("a custom vector shows the Custom marker and auto-opens advanced weights", () => {
    const { container } = renderSection({
      ...OFF,
      intelligentEnabled: true,
      weights: { cost: 0.33, quality: 0.34, latency: 0.33 },
    });
    expect(
      container.querySelector('[data-slot="routing-preset-custom"]'),
    ).not.toBeNull();
    // Advanced weights are open without clicking the toggle.
    expect(
      container.querySelector('[data-slot="routing-weights"]'),
    ).not.toBeNull();
  });

  it("advanced weights are collapsed by default for a known preset", () => {
    const { container } = renderSection({ ...OFF, intelligentEnabled: true });
    expect(container.querySelector('[data-slot="routing-weights"]')).toBeNull();
    fireEvent.click(
      container.querySelector(
        '[data-slot="routing-advanced-toggle"]',
      ) as HTMLElement,
    );
    expect(
      container.querySelector('[data-slot="routing-weights"]'),
    ).not.toBeNull();
  });
});

describe("RoutingSection — budget caps (D-31-2 / D-31-X-empty-cap-input)", () => {
  it("a blank cap input emits null, never 0", () => {
    const { container, onChange } = renderSection({
      ...OFF,
      intelligentEnabled: true,
      budget: { ...OFF.budget, maxCentsPerTurn: 2.5 },
    });
    const budget = container.querySelector(
      '[data-slot="routing-budget"]',
    ) as HTMLElement;
    const perTurn = within(budget).getByLabelText(/per turn/i);
    fireEvent.change(perTurn, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        budget: expect.objectContaining({ maxCentsPerTurn: null }),
      }),
    );
  });

  it("shows the per-day fail-loud warning only when a per-day cap is set", () => {
    const { container: withoutCap } = renderSection({
      ...OFF,
      intelligentEnabled: true,
    });
    expect(
      withoutCap.querySelector('[data-slot="routing-perday-warning"]'),
    ).toBeNull();

    const { container: withCap } = renderSection({
      ...OFF,
      intelligentEnabled: true,
      budget: { ...OFF.budget, maxCentsPerDay: 100 },
    });
    expect(
      withCap.querySelector('[data-slot="routing-perday-warning"]'),
    ).not.toBeNull();
  });
});
