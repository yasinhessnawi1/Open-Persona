/**
 * F3 T23 — Playwright pass scaffold (OPERATOR-PASSED at close-out).
 *
 * Per D-F3-X-closeout-operator-pass-convention: this scaffold codifies
 * the five live E2E journeys F3 ships, but the run itself is
 * operator-passed because it requires DEEPSEEK_API_KEY + Postgres
 * (`docker start persona-pg`) + local API (`bash run-local.sh`) +
 * dev server (`pnpm dev`) provisioned. CI cannot provision all four
 * atomically.
 *
 * **Honest disposition** (mirrors Spec 13 fold-in-#9 + Spec 14 T22b):
 *   - The scaffold exists on disk; the structure is verifiable.
 *   - The operator runs the five journeys at Phase 6 close-out with
 *     the live stack provisioned and records per-journey results in
 *     state.md's "Manual E2E results" section under the 🟦 marker.
 *   - The criterion-#11 human design review pairs with this; both
 *     gate Phase 6 sign-off per the workflow override.
 *
 * **Journeys (each = one §10 acceptance criterion + cross-cutting concerns):**
 *
 *   1. Image attach + preview + send + bubble render (§10 #1, #2, Concern #2)
 *      - drag a real PNG fixture onto the composer
 *      - verify preview thumbnail appears
 *      - send the message
 *      - verify the image renders in the sent bubble via useAuthedImageBlobUrl
 *      - **NETWORK INSPECTION:** the POST /messages body contains
 *        `images: [{workspace_path, media_type}]` and NO base64 string
 *        (the T22 structural invariant verified live).
 *
 *   2. Document upload + chip persists across turns (§10 #3, Concern #2)
 *      - attach a real PDF
 *      - verify the chip appears in the conversation panel
 *      - send 2+ messages
 *      - verify the chip persists in the panel through every turn
 *      - verify the persona's reply references document content
 *
 *   3. Document list + remove (§10 #4)
 *      - GET /v1/conversations/:id/documents returns the attached doc
 *      - click the chip's X → DELETE fires + chip disappears
 *      - subsequent messages: persona no longer draws on the doc
 *
 *   4. Fail-loud no-vision (§10 #7 — THE LOAD-BEARING TEST, Concern #3)
 *      - configure a fixture persona with NO vision tier (DeepSeek-only)
 *      - load the chat page
 *      - verify attach button is `disabled` with the deployment-honest
 *        tooltip ("Image attachments aren't available on this deployment")
 *      - documents attach still works (the button isn't fully disabled)
 *
 *   5. Mobile viewport (§10 #12)
 *      - load chat at 375x667
 *      - verify no horizontal scroll
 *      - verify attach button is tappable
 *      - verify the document panel renders inline (or sheet, per F2)
 */

import { expect, test } from "@playwright/test";

// Mark every spec in this file as the F3 operator-passed scaffold.
test.describe.configure({ mode: "serial" });

test.skip("operator-pass — see docs/specs/phase2/spec_F3/state.md", () => {
  // This file is the scaffold per D-F3-X-closeout-operator-pass-convention.
  // The actual journeys are operator-run at Phase 6 with the live stack
  // provisioned (DEEPSEEK_API_KEY + Postgres + API + dev server) and
  // results captured in state.md "Manual E2E results" section under 🟦.
  //
  // To activate live runs (operator workflow):
  //   1. docker start persona-pg
  //   2. cd packages/api && bash run-local.sh
  //   3. cd packages/web && pnpm dev   (in a separate terminal)
  //   4. Remove this .skip() and run `pnpm exec playwright test f3-file-input`
  //   5. Record per-journey outcomes (🟦 pass / 🟥 fail with screenshots)
  //      in docs/specs/phase2/spec_F3/state.md "Manual E2E results"
});

// Journey scaffolds — each is marked skipped pending operator pass.
// The expect.toBe(true) bodies are placeholders; the operator wires
// real interactions when activating each journey.

test.skip("Journey 1 — image attach + preview + send + bubble render + body inspection", async () => {
  // §10 #1 + #2 + Concern #2 + Concern #4 structural defence
  expect(true).toBe(true);
});

test.skip("Journey 2 — document upload + chip persists across turns", async () => {
  // §10 #3 + Concern #2
  expect(true).toBe(true);
});

test.skip("Journey 3 — document list + remove + persona stops drawing", async () => {
  // §10 #4
  expect(true).toBe(true);
});

test.skip("Journey 4 — fail-loud no-vision (THE load-bearing test, Concern #3)", async () => {
  // §10 #7 — the binary criterion
  expect(true).toBe(true);
});

test.skip("Journey 5 — mobile viewport (375x667, no horizontal scroll, tap targets)", async () => {
  // §10 #12
  expect(true).toBe(true);
});
