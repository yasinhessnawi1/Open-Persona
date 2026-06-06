import { expect, test } from "@playwright/test";

/**
 * Spec F2 T31 — Settings (F2 rebuild) e2e.
 *
 * Asserts the F2 presentation contract for the settings page: PageBody +
 * PageHeader + Stack of cards (account / credits / preferences / usage)
 * with retokenised typography. Theme toggle behaviour preserved (covered
 * by the existing shell.spec.ts in detail; this spec asserts the F2 data
 * slots + the low-balance / credits-exhausted surfaces).
 *
 * Plumbing untouched (per audit.md §settings.plumbing).
 */

test.describe("F2 T31 settings", () => {
  test("composes PageBody + PageHeader + Stack of section cards", async ({
    page,
  }) => {
    await page.goto("/settings");

    await expect(page.locator('[data-slot="page-body"]')).toBeVisible();
    await expect(page.locator('[data-slot="page-header"]')).toBeVisible();
    await expect(page.locator('[data-slot="page-header-title"]')).toContainText(
      "Settings",
    );
    await expect(page.locator('[data-slot="settings-account"]')).toBeVisible();
    await expect(
      page.locator('[data-slot="settings-preferences"]'),
    ).toBeVisible();
    await expect(page.locator('[data-slot="settings-usage"]')).toBeVisible();
  });

  test("credit balance renders in .type-display (Fraunces hero scale)", async ({
    page,
  }) => {
    await page.goto("/settings");

    // Either credits-balance is visible (positive balance) OR the 402
    // ErrorState surface is visible (zero balance).
    const balance = page.locator('[data-slot="settings-credits-balance"]');
    const exhaustedState = page.locator('[data-slot="error-state"]');

    if (await balance.isVisible().catch(() => false)) {
      const fontFamily = await balance.evaluate((el) =>
        getComputedStyle(el).fontFamily.toLowerCase(),
      );
      expect(fontFamily).toContain("fraunces");
    } else {
      await expect(exhaustedState).toBeVisible();
      await expect(exhaustedState).toHaveAttribute("data-status", "402");
    }
  });

  test("theme toggle persists across reload", async ({ page }) => {
    await page.goto("/settings");

    // Pick the Dark theme option in the segmented control.
    await page
      .locator('[data-slot="settings-theme-option"]')
      .filter({ hasText: /Dark/i })
      .click();

    // The <html> element should pick up the `dark` class via next-themes.
    await expect(page.locator("html")).toHaveClass(/dark/);

    // Reload and verify persistence.
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);

    // Reset to light so subsequent specs aren't affected.
    await page
      .locator('[data-slot="settings-theme-option"]')
      .filter({ hasText: /Light/i })
      .click();
  });

  test("tier-badge switch toggles aria-checked", async ({ page }) => {
    await page.goto("/settings");

    const switchEl = page.locator('[data-slot="settings-switch"]');
    await expect(switchEl).toBeVisible();
    const initial = await switchEl.getAttribute("aria-checked");
    await switchEl.click();
    const after = await switchEl.getAttribute("aria-checked");
    expect(after).not.toBe(initial);
  });
});
