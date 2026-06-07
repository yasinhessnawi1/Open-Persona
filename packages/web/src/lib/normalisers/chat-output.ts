/**
 * Spec F4 T03 — Chat SSE → `OutputContent[]` normaliser.
 *
 * Maps the BARE-payload chat SSE frames (D-09-1) onto the
 * `OutputContent` discriminated union (D-F4-X-renderer-normaliser-shape).
 * Transport-shape leakage stops HERE — renderers downstream never see
 * a ChatEvent. The run-side mirror at `./run-output.ts` (T04) peels
 * the RunEvent `.data` envelope and otherwise emits IDENTICAL
 * `OutputContent` via the shared `./_classify.ts` helpers.
 *
 * Per-event dispatch:
 *   - `chunk` / `done`: text-rendering / terminal frames → [].
 *   - `tool_calling`: emit one `working` per recognized capability tool;
 *     unrecognized tools silently fall through (handled by the existing
 *     text + tool-card path that D-F2-15 InterleavedContent already
 *     renders).
 *   - `tool_result`: `is_error` → `failure`; structured `produced_files`
 *     → classify each; otherwise → `result-block` (pre-T02b safety net).
 */

import type { OutputContent } from "@/lib/api/output-content";
import type { ChatEvent } from "@/lib/sse-types";

import { projectToolCalling, projectToolResult } from "./_classify";

export function chatSseToOutputContent(event: ChatEvent): OutputContent[] {
  switch (event.event) {
    case "chunk":
    case "done":
      return [];
    case "tool_calling":
      return projectToolCalling(event.data.tool_calls);
    case "tool_result":
      return projectToolResult(event.data);
  }
}
