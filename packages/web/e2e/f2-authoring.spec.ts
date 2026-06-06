import { expect, test } from "@playwright/test";

/**
 * Spec F2 T29 — Authoring (F2 rebuild) e2e.
 *
 * Asserts the F2 presentation contract for the AuthorWizard: PageBody
 * + describe-phase Stack + retokenised typography + example chips +
 * Generate CTA. The full draft→refine→save cycle against live DeepSeek
 * is gated on the user's live-smoke (per the original pre-flight pattern);
 * this spec covers the deterministic presentation surface.
 *
 * Plumbing untouched: existing authoring.spec.ts continues to cover the
 * `/v1/personas/author` + `/v1/personas/author/refine` round-trip + the
 * 3-round refine cap; this spec is presentation-focused.
 */

test.describe("F2 T29 authoring (describe phase)", () => {
  test("composes PageBody + describe Stack with retokenised typography", async ({
    page,
  }) => {
    await page.goto("/personas/new");

    await expect(page.locator('[data-slot="page-body"]')).toBeVisible();
    await expect(
      page.locator('[data-slot="author-wizard-describe"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-slot="author-wizard-title"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-slot="author-wizard-description"]'),
    ).toBeVisible();

    // Generate button starts disabled (no description typed yet).
    const generate = page.locator('[data-slot="author-wizard-generate"]');
    await expect(generate).toBeVisible();
    await expect(generate).toBeDisabled();
  });

  test("example chip click pre-fills the description + enables Generate", async ({
    page,
  }) => {
    await page.goto("/personas/new");

    const firstExample = page
      .locator('[data-slot="author-wizard-example"]')
      .first();
    await expect(firstExample).toBeVisible();
    const exampleText = (await firstExample.textContent())?.trim() ?? "";
    expect(exampleText.length).toBeGreaterThan(0);

    await firstExample.click();
    const description = page.locator('[data-slot="author-wizard-description"]');
    await expect(description).toHaveValue(exampleText);

    const generate = page.locator('[data-slot="author-wizard-generate"]');
    await expect(generate).toBeEnabled();
  });

  test("title uses the F1 Fraunces .type-display token (font-family resolves)", async ({
    page,
  }) => {
    await page.goto("/personas/new");

    const title = page.locator('[data-slot="author-wizard-title"]');
    await expect(title).toBeVisible();
    const fontFamily = await title.evaluate((el) =>
      getComputedStyle(el).fontFamily.toLowerCase(),
    );
    expect(fontFamily).toContain("fraunces");
  });
});
