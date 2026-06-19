"use server";

import { redirect } from "next/navigation";
import { unwrap } from "@/lib/api";
import { serverApi } from "@/lib/api/server";

/**
 * Spec 35 — Tasks-page dispatch. Unlike the per-persona `startRun` (bound to a
 * single persona), this reads BOTH the chosen persona and the task from the
 * form, dispatches the agentic run, and jumps to the run viewer.
 */
export async function startTask(formData: FormData) {
  const personaId = String(formData.get("persona_id") ?? "").trim();
  const task = String(formData.get("task") ?? "").trim();
  if (!personaId || !task) return; // the form disables submit on empty input.
  const api = await serverApi();
  const run = await unwrap(
    await api.POST("/v1/personas/{persona_id}/runs", {
      params: { path: { persona_id: personaId } },
      body: { task },
    }),
  );
  redirect(`/runs/${run.id}`);
}
