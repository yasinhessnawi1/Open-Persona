/**
 * Hand-mirrored SSE event shapes (D-09-1). OpenAPI cannot model SSE streams, so
 * these are mirrored BY HAND from the frozen Pydantic models on the API side.
 * Keep in sync with:
 *   - chat events:  packages/api/src/persona_api/services/chat_service.py (_sse)
 *   - run events:   packages/runtime/src/persona_runtime/agentic/events.py (RunEvent)
 *
 * ⚠ The two streams use DIFFERENT envelopes (research.md §2.3 — the #1 silent-bug
 * risk):
 *   - CHAT frames serialise the BARE payload: `data:` IS the payload.
 *       e.g.  event: tool_result\ndata: {"tool_name","is_error","content"}
 *   - RUN frames serialise the WHOLE RunEvent: `data:` is
 *       {type, step, data, timestamp} — the payload is nested under `.data`.
 *       e.g.  event: tool_result\ndata: {"type":"tool_result","step":0,
 *               "data":{"tool_name","is_error","content"},"timestamp":"..."}
 *
 * Both streams share the SAME tool-event vocabulary (tool_calling / tool_result)
 * after the spec-08 chat-SSE patch (D-09-12).
 */

import type { RawSSEEvent } from "./sse";

// ----- shared tool-event payloads (identical in chat + run) -----

export interface ToolCallPayload {
  name: string;
  call_id: string;
  args: Record<string, unknown>;
}

export interface ToolCallingData {
  tool_names: string;
  tool_calls: ToolCallPayload[];
}

/**
 * One produced file in a `tool_result` event's structured payload.
 *
 * Wire-shape mirror of the runtime forwarding amendment at
 * `packages/runtime/src/persona_runtime/agentic/events.py:96-103`
 * (D-F4-X-event-kind-for-produced-files, Spec F4 T02b). The runtime
 * forwards `ToolResult.data["produced_files"]` onto the event payload
 * verbatim; this interface mirrors the dict shape the sandbox tool
 * factory populates at
 * `packages/core/src/persona/sandbox/tool.py:269-279`.
 *
 * Additive — absent on pre-amendment frames and on tools that don't
 * produce files (web_search, file_*, etc.). The F4 chat + run normalisers
 * read this when present; absence falls back to a `result-block` render.
 */
export interface ProducedFileRef {
  path: string;
  size_bytes: number;
  media_type?: string | null;
}

export interface ToolResultData {
  tool_name: string;
  is_error: boolean;
  content: string;
  /**
   * D-F4-X-event-kind-for-produced-files: structured produced files
   * surfaced from sandbox-backed tools (Spec 12 stdout-only / Spec 16
   * docx-pptx-xlsx-pdf / Spec 17 charts). Omitted on tools that don't
   * produce files and on pre-T02b frames (back-compat: F4 normalisers
   * fall back to a result-block render when absent).
   */
  produced_files?: ProducedFileRef[];
}

// =================== CHAT stream (bare-payload frames) ===================

export interface ChatChunkData {
  delta: string;
  is_final: boolean;
}

export interface ChatDoneData {
  // {} when usage is unavailable; otherwise the token counts.
  usage: { prompt_tokens?: number; completion_tokens?: number };
  tier: string;
  format_hints: Record<string, string>;
}

/** A parsed chat SSE event, discriminated by the `event:` name. */
export type ChatEvent =
  | { event: "chunk"; data: ChatChunkData }
  | { event: "tool_calling"; data: ToolCallingData }
  | { event: "tool_result"; data: ToolResultData }
  | { event: "done"; data: ChatDoneData };

const CHAT_EVENTS = new Set(["chunk", "tool_calling", "tool_result", "done"]);

/**
 * Parse one raw chat frame into a typed {@link ChatEvent}. `data` is the bare
 * payload. Returns null for unknown event names (forward-compatible). The wire
 * is our own trusted API, so we cast after a name check rather than deep-validate.
 */
export function parseChatEvent(raw: RawSSEEvent): ChatEvent | null {
  if (!CHAT_EVENTS.has(raw.event)) return null;
  const data = JSON.parse(raw.data) as unknown;
  return { event: raw.event, data } as ChatEvent;
}

// =================== RUN stream (RunEvent envelope frames) ===================

export type RunEventType =
  | "started"
  | "tier"
  | "thinking"
  | "tool_calling"
  | "tool_result"
  | "asking_user"
  | "user_responded"
  | "reasoning"
  | "completed"
  | "cancelled"
  | "max_steps"
  | "error"
  | "finished";

interface RunEventBase {
  step: number;
  timestamp: string;
}

type EmptyData = Record<string, never>;

export interface StartedData {
  task: string;
}
export interface TierData {
  tier: string;
}
export interface ReasoningData {
  content: string;
}
export interface AskingUserData {
  question: string;
}
export interface CompletedData {
  output: string;
}
export interface MaxStepsData {
  summary: string;
}
export interface RunErrorData {
  message: string;
}
export interface FinishedData {
  run_id: string;
  status: string;
}

/** A parsed run SSE event — the full RunEvent envelope, discriminated by `type`. */
export type RunEvent =
  | (RunEventBase & { type: "started"; data: StartedData })
  | (RunEventBase & { type: "tier"; data: TierData })
  | (RunEventBase & { type: "thinking"; data: EmptyData })
  | (RunEventBase & { type: "tool_calling"; data: ToolCallingData })
  | (RunEventBase & { type: "tool_result"; data: ToolResultData })
  | (RunEventBase & { type: "asking_user"; data: AskingUserData })
  | (RunEventBase & { type: "user_responded"; data: EmptyData })
  | (RunEventBase & { type: "reasoning"; data: ReasoningData })
  | (RunEventBase & { type: "completed"; data: CompletedData })
  | (RunEventBase & { type: "cancelled"; data: EmptyData })
  | (RunEventBase & { type: "max_steps"; data: MaxStepsData })
  | (RunEventBase & { type: "error"; data: RunErrorData })
  | (RunEventBase & { type: "finished"; data: FinishedData });

/** The terminal frame the run stream emits after `finished` (`event: end`). */
export const SSE_RUN_END_EVENT = "end";

/**
 * Parse one raw run frame into a typed {@link RunEvent}. `data` is the full
 * RunEvent envelope (payload nested under `.data`). Returns null for the
 * terminal `end` frame and for malformed frames; the caller stops on `end`.
 */
export function parseRunEvent(raw: RawSSEEvent): RunEvent | null {
  if (raw.event === SSE_RUN_END_EVENT) return null;
  const env = JSON.parse(raw.data) as { type?: unknown };
  if (typeof env.type !== "string") return null;
  return env as RunEvent;
}
