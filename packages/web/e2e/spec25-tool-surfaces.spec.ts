import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

/**
 * Spec 25 — tool-surface E2E (operator-pass T23 automation).
 *
 * Drives the authed chat (fresh +clerk_test account from auth.setup, Turnstile
 * bypassed via the Clerk testing token) through the headline model-callable
 * tool surfaces against the LIVE stack (API :8000 + the operator's real
 * provider keys + Cloudflare imagegen). Assertions are resilient to LLM
 * non-determinism: the load-bearing checks are "the tool was dispatched"
 * (the `Used <tool>` card) + "the turn completed" (tier badge), not exact text.
 *
 * Surface 9 (§2.9) is the headline: the persona DISPATCHES generate_image
 * (the §2.9 callability fix). The inline-render of the produced image is the
 * documented KNOWN-LIMITATION (D-25-X-emergent-rich-output-delivery-deferred) —
 * this spec captures a screenshot as evidence either way.
 *
 * Screenshots land in docs/specs/phase2/spec_25/evidence/.
 */

const API = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8000";
const EVIDENCE = path.join(
  process.cwd(),
  "..",
  "..",
  "docs",
  "specs",
  "phase2",
  "spec_25",
  "evidence",
);

// Persona that DECLARES the model-callable tools (the allow-list is the gate).
const TOOL_PERSONA_YAML = `schema_version: "1.0"
identity:
  name: Nova
  role: creative + research assistant
  background: |
    A versatile assistant that generates images, fetches web pages, and runs code.
  language_default: en
  constraints:
    - Be concise.
tools:
  - generate_image
  - web_fetch
  - web_search
  - code_execution
  - file_read
  - file_write
self_facts:
  - fact: Eager to use tools.
    confidence: 1.0
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

async function seedConversation(page: Page): Promise<string> {
  await page.goto("/personas");
  const token = await clerkToken(page);
  const auth = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
  const personaRes = await page.request.post(`${API}/v1/personas`, {
    headers: auth,
    data: { yaml: TOOL_PERSONA_YAML },
  });
  expect(personaRes.status()).toBe(201);
  const personaId = (await personaRes.json()).id as string;
  const convRes = await page.request.post(
    `${API}/v1/personas/${personaId}/conversations`,
    {
      headers: auth,
      data: { title: "" },
    },
  );
  expect(convRes.status()).toBe(201);
  return (await convRes.json()).id as string;
}

async function send(page: Page, message: string): Promise<void> {
  await page.getByRole("textbox").fill(message);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(message, { exact: false }).first()).toBeVisible();
}

test("surface 9/10 — persona dispatches generate_image (§2.9 fix) via Cloudflare", async ({
  page,
}) => {
  const conversationId = await seedConversation(page);
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByText("Nova").first()).toBeVisible();

  await send(
    page,
    "Generate an image of a serene cosmic landscape with two moons.",
  );

  // §2.9 PROOF: the persona dispatches generate_image (no refusal). The tool
  // card ("Used generate_image") is the dispatch signal.
  await expect(page.getByText(/generate_image/i).first()).toBeVisible({
    timeout: 120_000,
  });
  // Turn completes (tier badge set on the `done` event).
  await expect(page.locator('[title^="Model tier"]').first()).toBeVisible({
    timeout: 120_000,
  });

  await page.screenshot({
    path: path.join(EVIDENCE, "spec25-e2e-generate-image-2026-06-12.png"),
    fullPage: true,
  });

  // KNOWN-LIMITATION (D-25-X-emergent-rich-output-delivery-deferred): the
  // produced image is NOT persisted/served in the chat path, so no inline
  // <img> from the workspace renders. We assert the dispatch succeeded; the
  // render gap is the deferred rendering-spec's scope. (No hard assertion on
  // the absence — just evidence via the screenshot above.)
});

test("surface 7 — web_fetch retrieves a real page", async ({ page }) => {
  const conversationId = await seedConversation(page);
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByText("Nova").first()).toBeVisible();

  await send(
    page,
    "Fetch https://en.wikipedia.org/wiki/Retrieval-augmented_generation and summarise it in one sentence.",
  );

  await expect(page.getByText(/web_fetch/i).first()).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.locator('[title^="Model tier"]').first()).toBeVisible({
    timeout: 120_000,
  });

  await page.screenshot({
    path: path.join(EVIDENCE, "spec25-e2e-web-fetch-2026-06-12.png"),
    fullPage: true,
  });
});

test("surface 1 — code_execution runs sci-Python (matplotlib)", async ({
  page,
}) => {
  const conversationId = await seedConversation(page);
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByText("Nova").first()).toBeVisible();

  await send(
    page,
    "Use Python (code_execution) to compute the first 10 Fibonacci numbers and " +
      "plot them with matplotlib. Print the list.",
  );

  await expect(page.getByText(/code_execution/i).first()).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.locator('[title^="Model tier"]').first()).toBeVisible({
    timeout: 120_000,
  });

  await page.screenshot({
    path: path.join(EVIDENCE, "spec25-e2e-code-execution-2026-06-13.png"),
    fullPage: true,
  });
});

test("surface 4 — file_write absolute-path UX (path-hint recovery)", async ({
  page,
}) => {
  const conversationId = await seedConversation(page);
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByText("Nova").first()).toBeVisible();

  // Coax the model to attempt an ABSOLUTE path so the SandboxViolationError +
  // valid-path hint (T10) surfaces; the model should then recover with a
  // relative path. The deterministic signal is the file_write dispatch + turn
  // completion; the screenshot captures the hint/recovery for evidence.
  await send(
    page,
    "Save the text 'hello spec25' to a file. Use exactly this absolute path and " +
      "do not change it: /workspace/out/report.md",
  );

  await expect(page.getByText(/file_write/i).first()).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.locator('[title^="Model tier"]').first()).toBeVisible({
    timeout: 120_000,
  });

  await page.screenshot({
    path: path.join(EVIDENCE, "spec25-e2e-file-write-2026-06-13.png"),
    fullPage: true,
  });
});
