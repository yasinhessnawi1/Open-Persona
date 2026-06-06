import { ArrowLeft, MessageSquare, Pencil, Shield } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { Grid, PageBody, Section, Stack } from "@/components/layout";
import { PersonaIdentityHeader } from "@/components/persona/persona-identity-header";
import { StartRunForm } from "@/components/personas/start-run-form";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { unwrap } from "@/lib/api";
import { serverApi } from "@/lib/api/server";
import { parsePersonaYaml } from "@/lib/persona";
import { cn } from "@/lib/utils";
import { startChat, startRun } from "./actions";

/**
 * Spec F2 T28 — Persona detail screen (rebuilt).
 *
 * Strangler-fig replace of the scaffold's detail JSX.
 *
 * DO NOT TOUCH (per audit.md §personas-detail.plumbing):
 *   - `serverApi()` GET `/v1/personas/{persona_id}` + `parsePersonaYaml(yaml)`
 *     + `notFound()` on 404 (Next 16 async-params contract);
 *   - `./actions.ts` server actions (`startChat`, `startRun`);
 *   - `<StartRunForm>` (composed verbatim from `components/personas/`).
 *
 * REPLACED (presentation only):
 *   - hand-rolled `<header>` with `<Avatar>` + `bg-primary/10` fallback
 *     (the D-F1-5 violation called out in audit.md §personas-detail.plumbing
 *     line 47) → T13 `<PersonaIdentityHeader size="lg">` composing T06
 *     `<PersonaAvatar>` so the avatar fill becomes per-persona identity-coloured;
 *   - hand-rolled `mx-auto max-w-3xl px-… py-…` → T20 `<PageBody>`;
 *   - inline `<Section title>` (Card + uppercase-tracking-wide heading) →
 *     T20 `<Section heading>` wrapping a T04 retokenised `<Card>` body so
 *     the section headings carry F2's Fraunces `.type-heading` voice;
 *   - inline `<Empty>` + `<Chips>` → muted body-text + retokenised `<Badge>`;
 *   - `text-[0.65rem]` epistemic badge (audit.md line 136 violation) →
 *     `.type-caption font-mono uppercase` so the badge resolves through F1's
 *     `--text-caption-*` tokens.
 *
 * The "Run task" callout retains the `border-primary/20` accent (vermilion
 * is the brand cue; this is THE primary CTA on the detail surface).
 */
export default async function PersonaDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const t = await getTranslations("personas");
  const api = await serverApi();
  const res = await api.GET("/v1/personas/{persona_id}", {
    params: { path: { persona_id: id } },
  });
  if (res.response.status === 404) notFound();
  const detail = await unwrap(res);
  const p = parsePersonaYaml(detail.yaml);

  const headerPersona = {
    id: detail.id,
    name: p.name,
    role: p.role,
    avatar_url: detail.avatar_url ?? null,
  };

  return (
    <PageBody>
      <Link
        href="/personas"
        className="type-ui mb-6 inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
        data-slot="back-link"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        {t("backToList")}
      </Link>

      <header
        className="mb-8 flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between"
        data-slot="persona-detail-header"
      >
        <PersonaIdentityHeader persona={headerPersona} size="lg" />
        <div className="flex shrink-0 flex-wrap items-center gap-3">
          <Badge
            variant="secondary"
            className="type-caption font-mono uppercase"
          >
            {p.languageDefault}
          </Badge>
          <form action={startChat.bind(null, id)}>
            <button type="submit" className={cn(buttonVariants(), "gap-2")}>
              <MessageSquare className="size-4" aria-hidden="true" />
              {t("startChat")}
            </button>
          </form>
          <Link
            href={`/personas/${id}/edit`}
            className={cn(buttonVariants({ variant: "outline" }), "gap-2")}
          >
            <Pencil className="size-4" aria-hidden="true" />
            {t("edit")}
          </Link>
        </div>
      </header>

      <Stack gap={5}>
        <Card
          className="gap-3 border-primary/20 p-5"
          data-slot="persona-detail-run-task"
        >
          <h2 className="type-heading" data-slot="run-task-title">
            {t("runTaskTitle", { name: p.name })}
          </h2>
          <StartRunForm action={startRun.bind(null, id)} name={p.name} />
        </Card>

        {p.background ? (
          <Section heading={t("background")}>
            <Card className="p-5">
              <p className="type-body whitespace-pre-line text-muted-foreground">
                {p.background}
              </p>
            </Card>
          </Section>
        ) : null}

        <Section heading={t("constraints")}>
          <Card className="p-5">
            {p.constraints.length === 0 ? (
              <p className="type-body text-muted-foreground">{t("none")}</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {p.constraints.map((c) => (
                  <li key={c} className="type-body flex items-start gap-2">
                    <Shield
                      className="mt-0.5 size-4 shrink-0 text-primary"
                      aria-hidden="true"
                    />
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </Section>

        {p.selfFacts.length > 0 ? (
          <Section heading={t("selfFacts")}>
            <Card className="p-5">
              <ul className="type-body flex flex-col gap-1.5">
                {p.selfFacts.map((f) => (
                  <li key={f.fact} className="text-muted-foreground">
                    {f.fact}
                  </li>
                ))}
              </ul>
            </Card>
          </Section>
        ) : null}

        {p.worldview.length > 0 ? (
          <Section heading={t("worldview")}>
            <Card className="p-5">
              <ul className="type-body flex flex-col gap-2.5">
                {p.worldview.map((w) => (
                  <li
                    key={w.claim}
                    className="flex flex-wrap items-baseline gap-2"
                  >
                    <span>{w.claim}</span>
                    {w.epistemic ? (
                      <Badge
                        variant="outline"
                        className="type-caption font-mono uppercase"
                        data-slot="worldview-epistemic"
                      >
                        {w.epistemic}
                      </Badge>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Card>
          </Section>
        ) : null}

        {(p.tools.length > 0 || p.skills.length > 0) && (
          <Grid cols={{ base: 1, sm: 2 }} gap={5}>
            <Section heading={t("tools")}>
              <Card className="p-5">
                {p.tools.length === 0 ? (
                  <p className="type-body text-muted-foreground">{t("none")}</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {p.tools.map((i) => (
                      <Badge
                        key={i}
                        variant="secondary"
                        className="type-caption font-mono"
                      >
                        {i}
                      </Badge>
                    ))}
                  </div>
                )}
              </Card>
            </Section>
            <Section heading={t("skills")}>
              <Card className="p-5">
                {p.skills.length === 0 ? (
                  <p className="type-body text-muted-foreground">{t("none")}</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {p.skills.map((i) => (
                      <Badge
                        key={i}
                        variant="secondary"
                        className="type-caption font-mono"
                      >
                        {i}
                      </Badge>
                    ))}
                  </div>
                )}
              </Card>
            </Section>
          </Grid>
        )}
      </Stack>
    </PageBody>
  );
}
