"use server";

import { redirect } from "next/navigation";
import { serverApi } from "@/lib/api/server";

interface PydanticError {
  msg?: string;
}

function formatDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as PydanticError;
    if (typeof first.msg === "string") return first.msg;
  }
  return fallback;
}

/**
 * Persist an edited persona YAML (PATCH, re-validated server-side) and redirect
 * to its detail page (T08). Returns a structured error on validation failure so
 * the editor can surface it instead of crashing (redirect only on success).
 *
 * ``avatarUrl`` (a workspace ref from an avatar upload) rides the same PATCH; the
 * API leaves the avatar untouched when it is null/omitted, so an unchanged avatar
 * is preserved.
 */
export async function savePersona(
  personaId: string,
  yaml: string,
  avatarUrl?: string | null,
): Promise<{ error: string } | undefined> {
  const api = await serverApi();
  const res = await api.PATCH("/v1/personas/{persona_id}", {
    params: { path: { persona_id: personaId } },
    body: { yaml, avatar_url: avatarUrl ?? null },
  });
  if (res.error !== undefined) {
    const body = res.error as { error?: string; detail?: unknown };
    return {
      error: formatDetail(body.detail, body.error ?? "save_failed"),
    };
  }
  redirect(`/personas/${personaId}`);
}

/**
 * Set a persona's auto-dispatch consent (Spec 21 T09 / Spec 31 T6). Tri-state:
 * ``true`` = grant, ``false`` = decline, ``null`` = revoke back to "ask". An
 * inline settings toggle (no redirect); returns a structured error on failure so
 * the editor can revert its optimistic state.
 */
export async function setConsent(
  personaId: string,
  granted: boolean | null,
): Promise<{ error: string } | undefined> {
  const api = await serverApi();
  const res = await api.PATCH("/v1/personas/{persona_id}/consent", {
    params: { path: { persona_id: personaId } },
    body: { granted },
  });
  if (res.error !== undefined) {
    const body = res.error as { error?: string; detail?: unknown };
    return {
      error: formatDetail(body.detail, body.error ?? "consent_failed"),
    };
  }
  return undefined;
}

/**
 * Create a persona from a reviewed authoring draft (spec 10, D-10-2). Authoring
 * now returns a draft (no row); the user saves the reviewed YAML here, which
 * creates the persona and redirects to its detail page. Validation errors are
 * returned structured (redirect only on success).
 */
export async function createPersona(
  yaml: string,
): Promise<{ error: string } | undefined> {
  const api = await serverApi();
  const res = await api.POST("/v1/personas", { body: { yaml } });
  if (res.error !== undefined) {
    const body = res.error as { error?: string; detail?: unknown };
    return {
      error: formatDetail(body.detail, body.error ?? "save_failed"),
    };
  }
  redirect(`/personas/${res.data.id}`);
}
