import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { mapMcpCatalog } from "@/components/personas/mcp-catalog";
import { PersonaEditor } from "@/components/personas/persona-editor";
import { type ToolSummary, unwrap } from "@/lib/api";
import type { components } from "@/lib/api/schema";
import { serverApi } from "@/lib/api/server";
import { parsePersonaYaml } from "@/lib/persona";
import { savePersona, setConsent } from "@/lib/persona-actions";
import { yamlToDoc } from "@/lib/persona-draft";

export default async function EditPersonaPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params; // Next 16: params is async.
  const t = await getTranslations("author");
  const api = await serverApi();

  const personaRes = await api.GET("/v1/personas/{persona_id}", {
    params: { path: { persona_id: id } },
  });
  if (personaRes.response.status === 404) notFound();
  const detail = await unwrap(personaRes);

  const [tools, skills, mcpCatalog] = await Promise.all([
    unwrap(await api.GET("/v1/tools")),
    unwrap(await api.GET("/v1/skills")),
    unwrap(await api.GET("/v1/mcp-catalog")),
  ]);

  const doc = yamlToDoc(detail.yaml);
  const name = parsePersonaYaml(detail.yaml).name;

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6">
      <Link
        href={`/personas/${id}`}
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        {t("backToPersona")}
      </Link>
      <h1 className="mb-6 font-heading text-2xl font-semibold tracking-tight">
        {t("editTitle", { name })}
      </h1>
      <PersonaEditor
        initialDoc={doc}
        tools={(tools as ToolSummary[]).map((x) => x.name)}
        skills={(skills as ToolSummary[]).map((x) => x.name)}
        mcpServers={mapMcpCatalog(
          mcpCatalog as components["schemas"]["MCPCatalogServer"][],
        )}
        personaId={id}
        onSave={savePersona.bind(null, id)}
        saveLabel={t("save")}
        initialConsent={detail.consent_to_auto_dispatch ?? null}
        onConsentChange={setConsent.bind(null, id)}
      />
    </div>
  );
}
