/**
 * Spec F2 T24 — ThemeToggle tests.
 *
 * Verifies the tri-state toggle renders + exposes its trigger + the F2 motion
 * token consumption on the icon-swap is applied.
 */

import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { ThemeProvider } from "next-themes";
import { describe, expect, it } from "vitest";
import { ThemeToggle } from "./theme-toggle";

const messages = {
  theme: {
    toggle: "Toggle theme",
    light: "Light",
    dark: "Dark",
    system: "System",
  },
};

function renderToggle() {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ThemeProvider attribute="class">
        <ThemeToggle />
      </ThemeProvider>
    </NextIntlClientProvider>,
  );
}

describe("ThemeToggle", () => {
  it("renders the dropdown trigger with the translated aria-label", () => {
    const { container } = renderToggle();
    const trigger = container.querySelector(
      '[data-slot="theme-toggle-trigger"]',
    );
    expect(trigger).not.toBeNull();
    expect(trigger?.getAttribute("aria-label")).toBe("Toggle theme");
  });

  it("renders Sun + Moon icons with the F2 motion-fast transition", () => {
    const { container } = renderToggle();
    const icons = container.querySelectorAll(
      '[data-slot="theme-toggle-trigger"] svg',
    );
    expect(icons.length).toBe(2);
    for (const icon of Array.from(icons)) {
      const cls = icon.getAttribute("class") ?? "";
      expect(cls).toContain("transition-opacity");
      expect(cls).toContain("duration-[var(--motion-duration-fast)]");
    }
  });

  it("uses size-[1.2rem] (positional, documented in audit §grep-gate-seed)", () => {
    const { container } = renderToggle();
    const icons = container.querySelectorAll(
      '[data-slot="theme-toggle-trigger"] svg',
    );
    for (const icon of Array.from(icons)) {
      expect(icon.getAttribute("class")).toContain("size-[1.2rem]");
    }
  });
});
