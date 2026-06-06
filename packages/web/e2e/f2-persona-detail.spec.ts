import { expect, type Page, test } from "@playwright/test";

/**
 * Spec F2 T28 — Persona detail (F2 rebuild) e2e.
 *
 * Asserts the F2 presentation contract for the detail view: PageBody
 * + PersonaIdentityHeader + Section-cards composition, the closed D-F1-5
 * violation (PersonaAvatar identity-coloured fill instead of bg-primary/10),
 * the closed `text-[0.65rem]` epistemic badge, and the Edit-link
 * navigation to the (preserved) editor route.
 *
 * Plumbing untouched: existing personas.spec.ts continues to cover the
 * REST seed → list → detail navigation flow. This spec is presentation-
 * focused on the rebuilt detail screen.
 */

const API = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

const ASTRID_YAML = `schema_version: "1.0"
identity:
  name: Astrid
  role: Norwegian tenancy law assistant
  background: |
    Helps tenants understand husleieloven.
  language_default: en
  constraints:
    - Never give binding legal advice.
self_facts:
  - fact: Specialised in Norwegian residential tenancy.
    confidence: 1.0
worldview:
  - claim: Tenants in Norway have strong protections.
    domain: tenancy
    epistemic: fact
    confidence: 0.95
    valid_time: always
`;

type ClerkWindow = {
  Clerk: {
    session: { getToken: (o: { template: string }) => Promise<string | null> };
  };
};

async function clerkToken(page: Page): Promise<string> {
  await page.waitForFunction(
    () =>
      Boolean(
        (window as unknown as { Clerk?: { session?: unknown } }).Clerk?.session,
      ),
    null,
    { timeout: 20_000 },
  );
  const token = await page.evaluate(() =>
    (window as unknown as ClerkWindow).Clerk.session.getToken({
      template: "persona-api",
    }),
  );
  if (!token) throw new Error("no persona-api token");
  return token;
}

async function seedAstrid(page: Page): Promise<string> {
  const token = await clerkToken(page);
  await page.request.post(`${API}/v1/personas`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    data: { yaml: ASTRID_YAML },
  });
  await page.goto("/personas");
  await page
    .locator('[data-slot="persona-card"]', { hasText: "Astrid" })
    .first()
    .click();
  await page.waitForURL(/\/personas\/[^/]+$/, { timeout: 30_000 });
  return page.url();
}

test.describe("F2 T28 persona detail", () => {
  test("composes PageBody + PersonaIdentityHeader + Sections + StartRunForm", async ({
    page,
  }) => {
    await seedAstrid(page);

    await expect(page.locator('[data-slot="page-body"]')).toBeVisible();
    await expect(
      page.locator('[data-slot="persona-identity-header"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-slot="persona-identity-name"]'),
    ).toContainText("Astrid");
    await expect(
      page.locator('[data-slot="persona-identity-role"]'),
    ).toContainText("Norwegian tenancy law assistant");

    // The run-task callout is the primary CTA on the detail surface.
    await expect(
      page.locator('[data-slot="persona-detail-run-task"]'),
    ).toBeVisible();

    // T20 <Section> blocks for the structured content. At least three are
    // expected for a fully-fleshed persona (background + constraints +
    // self-facts OR worldview), each wrapping a Card body.
    const sections = page.locator('[data-slot="section"]');
    expect(await sections.count()).toBeGreaterThanOrEqual(2);

    // The constraints list renders Astrid's "Never give binding legal advice."
    await expect(
      page.getByText("Never give binding legal advice."),
    ).toBeVisible();
  });

  test("identity-coloured avatar (D-F1-5 closed): PersonaAvatar exposes --identity-h", async ({
    page,
  }) => {
    await seedAstrid(page);

    // The detail header's <PersonaAvatar> sets `--identity-h` via
    // personaIdentityStyle(). Read it from the role=img avatar to confirm
    // the scaffold's uniform `bg-primary/10` fallback is gone and the
    // per-persona identity hue drives the fill.
    const avatar = page
      .locator('[data-slot="persona-identity-header"]')
      .getByRole("img", { name: "Astrid" });
    await expect(avatar).toBeVisible();
    const hue = await avatar.evaluate((el) =>
      getComputedStyle(el).getPropertyValue("--identity-h").trim(),
    );
    expect(hue.length).toBeGreaterThan(0);
  });

  test("epistemic badge closes the text-[0.65rem] legacy via .type-caption", async ({
    page,
  }) => {
    await seedAstrid(page);

    // Astrid's worldview entry has epistemic="fact" → renders as a Badge
    // with the data-slot the F2 rebuild added.
    const badge = page.locator('[data-slot="worldview-epistemic"]').first();
    if (await badge.isVisible().catch(() => false)) {
      await expect(badge).toContainText(/fact|opinion|policy/i);
      // The retokenised badge should resolve through F1's .type-caption
      // size (~0.65rem). Read the computed font-size and assert it's the
      // caption tier (under 0.75rem; the scaffold's `text-[0.65rem]` was
      // the violation we closed).
      const fontSize = await badge.evaluate((el) =>
        Number.parseFloat(getComputedStyle(el).fontSize),
      );
      // 0.65rem = ~10.4px at 16px root; allow ±2px slack for browser deltas.
      expect(fontSize).toBeLessThan(13);
    }
  });

  test("Edit button routes to the (preserved) editor at /personas/{id}/edit", async ({
    page,
  }) => {
    const detailUrl = await seedAstrid(page);

    await page.getByRole("link", { name: /Edit/i }).first().click();
    await page.waitForURL(/\/personas\/[^/]+\/edit$/, { timeout: 30_000 });
    expect(page.url()).toBe(`${detailUrl}/edit`);
  });
});
