import type { ActivityEndData, ActivityStartData } from "@/lib/sse-types";

/**
 * P2 — the reduced "using <X>…" activity, the live-state half of the activity contract.
 *
 * This is a SEPARATE channel from the tool card (`RunStep.tools` / message `tools`):
 * `activity_start` opens an entry, `activity_end` resolves it by `activityId`. The tool
 * card stays sourced from `tool_result` during keep-both (P2-D-3), so a call renders ONE
 * card plus a transient activity state — never two cards. The component (T5) renders the
 * collapse-to-current live state from this list (P2-D-5); the full ordered trail lives in
 * the persisted RunEvent log.
 */
export interface ActivityView {
  activityId: string;
  kind: string;
  name: string;
  label: string;
  status: "running" | "ok" | "error" | "denied" | "awaiting_approval";
  durationMs?: number;
}

/**
 * Open an activity from its start event. Idempotent on replay (reattach re-streams the
 * persisted log): a duplicate `activity_id` is ignored, so re-applying the same frame
 * never doubles an entry.
 */
export function reduceActivityStart(
  prev: ActivityView[] | undefined,
  d: ActivityStartData,
): ActivityView[] {
  const list = prev ?? [];
  if (list.some((a) => a.activityId === d.activity_id)) return list;
  return [
    ...list,
    {
      activityId: d.activity_id,
      kind: d.kind,
      name: d.name,
      label: d.label,
      status: "running",
    },
  ];
}

/**
 * Resolve an activity from its end event — set the terminal `status` + duration on the
 * matching `activityId`. A no-op (returns the list unchanged) if no start was seen,
 * and idempotent on replay (re-applying maps to the same resolved entry).
 */
export function reduceActivityEnd(
  prev: ActivityView[] | undefined,
  d: ActivityEndData,
): ActivityView[] {
  const list = prev ?? [];
  return list.map((a) =>
    a.activityId === d.activity_id
      ? {
          ...a,
          status: normaliseStatus(d.status),
          durationMs: d.duration_ms,
        }
      : a,
  );
}

function normaliseStatus(status: string): ActivityView["status"] {
  switch (status) {
    case "ok":
    case "error":
    case "denied":
    case "awaiting_approval":
      return status;
    default:
      // Forward-compatible: an unknown terminal status resolves the spinner to a
      // neutral "ok" rather than leaving it spinning forever.
      return "ok";
  }
}
