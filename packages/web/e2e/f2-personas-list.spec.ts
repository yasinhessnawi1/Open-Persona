import { expect, type Page, test } from "@playwright/test";

/**
 * Spec F2 T27 — Persona list (F2 rebuild) e2e.
 *
 * Asserts the F2 presentation contract: PageBody + PageHeader + EmptyState
 * + Grid + PersonaCard composition, AND the §4 individuality proof
 * (Astrid + Kai + Maren read as three distinct identity colours).
 *
 * Plumbing untouched: the existing personas.spec.ts continues to cover the
 * REST seed + detail navigation flow. This spec is presentation-focused.
 */

const API = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

const PERSONAS = [
  {
    name: "Astrid",
    role: "Norwegian tenancy law assistant",
    yaml: `schema_version: "1.0"
identity:
  name: Astrid
  role: Norwegian tenancy law assistant
  background: |
    Helps tenants understand husleieloven.
  language_default: en
  constraints:
    - Never give binding legal advice.
self_facts: []
worldview: []
`,
  },
  {
    name: "Kai",
    role: "Marine biologist",
    yaml: `schema_version: "1.0"
identity:
  name: Kai
  role: Marine biologist
  background: |
    Studies coral reef adaptation.
  language_default: en
self_facts: []
worldview: []
`,
  },
  {
    name: "Maren",
    role: "Forest ecologist",
    yaml: `schema_version: "1.0"
identity:
  name: Maren
  role: Forest ecologist
  background: |
    Studies temperate forest succession.
  language_default: en
self_facts: []
worldview: []
`,
  },
] as const;

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

test.describe("F2 T27 persona list", () => {
  test("composes PageBody + PageHeader + EmptyState (when empty) OR Grid of PersonaCards (when seeded)", async ({
    page,
  }) => {
    await page.goto("/personas");

    // PageBody + PageHeader are always present regardless of empty/seeded.
    await expect(page.locator('[data-slot="page-body"]')).toBeVisible();
    await expect(page.locator('[data-slot="page-header"]')).toBeVisible();
    await expect(page.locator('[data-slot="page-header-title"]')).toContainText(
      "Personas",
    );

    const emptyState = page.locator('[data-slot="empty-state"]');
    if (await emptyState.isVisible().catch(() => false)) {
      // Honest empty state: title + description + action all present, with
      // the inviting (not apologetic) F1 voice.
      await expect(
        emptyState.locator('[data-slot="empty-state-title"]'),
      ).toContainText("No personas yet");
      await expect(
        emptyState.locator('[data-slot="empty-state-description"]'),
      ).toBeVisible();
      await expect(
        emptyState.locator('[data-slot="empty-state-action"]'),
      ).toBeVisible();
    } else {
      // Seeded state: at least one persona card via the F2 Grid.
      await expect(page.locator('[data-slot="grid"]')).toBeVisible();
      await expect(
        page.locator('[data-slot="persona-card"]').first(),
      ).toBeVisible();
    }
  });

  test("§4 proof: three personas render as three distinct identity colours", async ({
    page,
  }) => {
    // Seed Astrid + Kai + Maren so the §4 individuality proof has subjects
    // to read against. Other E2E tests may have already seeded some; the
    // create endpoint is idempotent on name+user so duplicates are skipped.
    const token = await clerkToken(page);
    for (const persona of PERSONAS) {
      await page.request.post(`${API}/v1/personas`, {
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        data: { yaml: persona.yaml },
      });
    }

    await page.goto("/personas");
    await expect(
      page.locator('[data-slot="persona-card"]').first(),
    ).toBeVisible();

    // For each of Astrid / Kai / Maren, the persona-card's inner
    // <PersonaAvatar> sets `--identity-h` (and l + c) on its own <span>
    // via `personaIdentityStyle()`. Scope to the matching card so the
    // shell-header avatar doesn't get picked up, then read the avatar's
    // identity hue. All three must be present, non-empty, and distinct.
    const hues: string[] = [];
    for (const persona of PERSONAS) {
      const card = page
        .locator('[data-slot="persona-card"]', { hasText: persona.name })
        .first();
      await expect(card).toBeVisible();
      const avatar = card.getByRole("img", { name: persona.name });
      await expect(avatar).toBeVisible();
      const hue = await avatar.evaluate((el) =>
        getComputedStyle(el).getPropertyValue("--identity-h").trim(),
      );
      hues.push(hue);
    }
    expect(hues.every((h) => h.length > 0)).toBe(true);
    expect(new Set(hues).size).toBe(PERSONAS.length);
  });

  test("clicking a persona card routes to its detail page", async ({
    page,
  }) => {
    // Ensure at least Astrid exists.
    const token = await clerkToken(page);
    await page.request.post(`${API}/v1/personas`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      data: { yaml: PERSONAS[0].yaml },
    });

    await page.goto("/personas");
    await page
      .locator('[data-slot="persona-card"]', { hasText: "Astrid" })
      .first()
      .click();
    await page.waitForURL(/\/personas\/[^/]+$/, { timeout: 30_000 });
  });
});
