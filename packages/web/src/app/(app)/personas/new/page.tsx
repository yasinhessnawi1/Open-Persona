import { PageBody } from "@/components/layout";
import { AuthorWizard } from "@/components/personas/author-wizard";
import { type ToolSummary, unwrap } from "@/lib/api";
import { serverApi } from "@/lib/api/server";

/**
 * Spec F2 T29 — Authoring page (rebuilt presentation).
 *
 * DO NOT TOUCH (per audit.md §authoring.plumbing):
 *   - `serverApi()` server-component fetch + parallel `GET /v1/tools` +
 *     `GET /v1/skills` (the existing draft-wire-up).
 *
 * REPLACED:
 *   - hand-rolled `mx-auto max-w-3xl px-… py-…` → T20 `<PageBody>`;
 *   - inner `<AuthorWizard>` uses its T29-rebuilt presentation (separate file).
 */
export default async function NewPersonaPage() {
  const api = await serverApi();
  const [tools, skills] = await Promise.all([
    unwrap(await api.GET("/v1/tools")),
    unwrap(await api.GET("/v1/skills")),
  ]);

  return (
    <PageBody>
      <AuthorWizard
        tools={(tools as ToolSummary[]).map((x) => x.name)}
        skills={(skills as ToolSummary[]).map((x) => x.name)}
      />
    </PageBody>
  );
}
