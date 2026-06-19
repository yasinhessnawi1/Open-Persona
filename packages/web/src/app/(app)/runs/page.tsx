import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { NewTaskForm } from "@/components/runs/new-task-form";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { unwrap } from "@/lib/api";
import { serverApi } from "@/lib/api/server";
import type { RunStatus } from "@/lib/run";
import { startTask } from "./actions";

/**
 * Spec 35 — the Tasks index. The entry point for agentic runs (previously
 * unreachable from the nav): dispatch a new task to any persona, and browse
 * previous runs (each links to the run viewer at `/runs/{id}`).
 */
export default async function TasksPage() {
  const t = await getTranslations("tasks");
  const api = await serverApi();
  const [runsRes, personasRes] = await Promise.all([
    api.GET("/v1/runs"),
    api.GET("/v1/personas"),
  ]);
  const runs = runsRes.data?.items ?? [];
  const personas = (await unwrap(personasRes)).map((p) => ({
    id: p.id,
    name: p.name,
  }));
  const nameById = new Map(personas.map((p) => [p.id, p.name]));

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8">
      <header className="mb-7">
        <p className="type-caption font-mono uppercase text-muted-foreground">
          {t("kicker")}
        </p>
        <h1 className="type-heading mt-1">{t("title")}</h1>
        <p className="type-ui mt-1.5 text-muted-foreground">{t("subtitle")}</p>
      </header>

      <section
        className="mb-9 rounded-lg border border-border bg-card p-4"
        data-slot="tasks-new"
      >
        <h2 className="type-ui mb-3 font-medium">{t("newHeading")}</h2>
        <NewTaskForm action={startTask} personas={personas} />
      </section>

      <section data-slot="tasks-previous">
        <h2 className="type-caption mb-3 font-mono uppercase text-muted-foreground">
          {t("previous")}
        </h2>
        {runs.length === 0 ? (
          <div className="rounded-lg border border-border border-dashed p-8 text-center">
            <p className="type-ui text-muted-foreground">{t("empty")}</p>
            <p className="type-caption mt-1 text-muted-foreground">
              {t("emptyHint")}
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {runs.map((r) => (
              <li key={r.id}>
                <Link
                  href={`/runs/${r.id}`}
                  aria-label={t("openRun", { task: r.task })}
                  className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 transition-colors hover:bg-muted/40"
                >
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <p className="type-ui truncate">{r.task}</p>
                    <p className="type-caption text-muted-foreground">
                      {nameById.get(r.persona_id) ?? t("unknownPersona")}
                    </p>
                  </div>
                  <RunStatusBadge
                    status={
                      (r.status === "awaiting_user"
                        ? "running"
                        : r.status) as RunStatus
                    }
                  />
                  <ChevronRight
                    className="size-4 shrink-0 text-muted-foreground"
                    aria-hidden
                  />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
