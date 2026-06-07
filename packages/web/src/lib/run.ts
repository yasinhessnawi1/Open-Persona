import type { ToolEntry } from "@/components/chat/tool-call-card";
import type { RunStatusResponse } from "@/lib/api";
import type { OutputContent } from "@/lib/api/output-content";
import {
  projectToolCalling,
  projectToolResult,
} from "@/lib/normalisers/_classify";
import type { RunEvent } from "@/lib/sse-types";

// Normalised run-viewer model (T07). Both the live `RunEvent` SSE stream and the
// persisted `GET /runs/:id` `steps[]` reduce into one {@link RunStep} shape so the
// timeline has a single render path.
//
// ⚠ `runs.steps` has TWO shapes depending on run state (verified against
// packages/api/.../background/run_worker.py):
//   - while RUNNING / on a crash-ERROR: the event-log snapshot — a list of
//     `RunEvent.model_dump` dicts `{type, step, data, timestamp}` (same shape as
//     the SSE frames).
//   - on COMPLETED / CANCELLED / MAX_STEPS: `Step.model_dump` dicts
//     `{type, tool_calls, results, question, user_answer, content, tier_used, …}`.
// {@link runViewFromSnapshot} discriminates per-entry and reduces accordingly.

/** Mirrors persona_runtime `RunStatus` plus the API's in-flight `"running"`. */
export type RunStatus =
  | "running"
  | "completed"
  | "cancelled"
  | "max_steps_reached"
  | "error";

const TERMINAL: ReadonlySet<RunStatus> = new Set([
  "completed",
  "cancelled",
  "max_steps_reached",
  "error",
]);

export function isTerminal(status: string): boolean {
  return TERMINAL.has(status as RunStatus);
}

/** One plan-act-reflect cycle, normalised for display. */
export interface RunStep {
  step: number;
  thinking: boolean;
  tools: ToolEntry[];
  /**
   * Spec F4 T04 (D-F4-X-output-derivation-shape): rich-output renderer
   * inputs derived view-time from this step's `tool_calling` +
   * `tool_result` events. Populated by {@link runViewFromEvents}; consumed
   * by `<StepCard>` (T11) through the renderer dispatcher (T09).
   *
   * Lifecycle:
   *   - `tool_calling` SETS the array to one `working` per recognized
   *     capability tool (image_gen / code_exec / doc_gen).
   *   - `tool_result` REPLACES the matching `working` (by tool name) with
   *     the projected outputs: `failure` on is_error, classified
   *     produced files on structured payload, or `result-block` on a
   *     plain-stdout result. Multiple produced files expand the slot.
   *   - Unrecognized capability tools (web_search, file_*, …) emit
   *     NOTHING to this array — they surface via the existing tool-card
   *     path. The F4 output surface is for rich outputs only.
   *
   * Top-level RunEvent `error` does NOT push here — it surfaces via the
   * existing {@link error} field. Step-card has its own error display
   * surface; doubling up would be noise.
   */
  outputs: OutputContent[];
  reasoning?: string;
  question?: string;
  answered: boolean;
  final?: string;
  maxSteps?: string;
  error?: string;
  tier?: string;
}

/** The whole run, as the viewer renders it. */
export interface RunView {
  task: string;
  status: RunStatus;
  tier?: string;
  steps: RunStep[];
  output?: string;
  error?: string;
}

function emptyStep(step: number): RunStep {
  return { step, thinking: false, tools: [], outputs: [], answered: false };
}

// ----- RunEvent reduction (live stream + the running/error snapshot) -----

/**
 * Reduce an ordered `RunEvent` list into a {@link RunView}. Keyed by step index,
 * so replaying overlapping events (SSE replays from the start of the buffered
 * queue; reconnects re-seed) is idempotent — `tool_calling` SETS the step's tool
 * list rather than appending.
 */
export function runViewFromEvents(
  events: readonly RunEvent[],
  base: { task: string },
): RunView {
  const map = new Map<number, RunStep>();
  let tier: string | undefined;
  let status: RunStatus = "running";
  let task = base.task;
  let output: string | undefined;
  let error: string | undefined;

  const ensure = (s: number): RunStep => {
    let st = map.get(s);
    if (!st) {
      st = emptyStep(s);
      map.set(s, st);
    }
    return st;
  };

  for (const ev of events) {
    switch (ev.type) {
      case "started":
        task = ev.data.task;
        break;
      case "tier":
        tier = ev.data.tier;
        break;
      case "thinking":
        ensure(ev.step).thinking = true;
        break;
      case "tool_calling": {
        const st = ensure(ev.step);
        st.thinking = false;
        st.tools = ev.data.tool_calls.map((c) => ({
          toolName: c.name,
          args: c.args,
          pending: true,
        }));
        // F4 T04: seed step.outputs with one `working` per recognized
        // capability tool. Unrecognized tools contribute nothing — their
        // result surfaces through st.tools' tool-card path.
        st.outputs = projectToolCalling(ev.data.tool_calls);
        break;
      }
      case "tool_result": {
        const st = ensure(ev.step);
        for (let i = st.tools.length - 1; i >= 0; i--) {
          if (
            st.tools[i].toolName === ev.data.tool_name &&
            st.tools[i].pending
          ) {
            st.tools[i] = {
              ...st.tools[i],
              result: ev.data.content,
              isError: ev.data.is_error,
              pending: false,
            };
            break;
          }
        }
        // F4 T04: replace the matching pending `working` in st.outputs with
        // the projected result (failure / classified produced files /
        // result-block). Mirror the tool_call matching: search backward by
        // label === tool_name; the last unresolved working wins (handles
        // parallel calls of the same capability tool).
        for (let i = st.outputs.length - 1; i >= 0; i--) {
          const item = st.outputs[i];
          if (item.kind === "working" && item.label === ev.data.tool_name) {
            st.outputs.splice(i, 1, ...projectToolResult(ev.data));
            break;
          }
        }
        break;
      }
      case "asking_user": {
        const st = ensure(ev.step);
        st.thinking = false;
        st.question = ev.data.question;
        break;
      }
      case "user_responded":
        ensure(ev.step).answered = true;
        break;
      case "reasoning": {
        const st = ensure(ev.step);
        st.thinking = false;
        st.reasoning = ev.data.content;
        break;
      }
      case "completed": {
        const st = ensure(ev.step);
        st.thinking = false;
        st.final = ev.data.output;
        output = ev.data.output;
        status = "completed";
        break;
      }
      case "max_steps": {
        const st = ensure(ev.step);
        st.thinking = false;
        st.maxSteps = ev.data.summary;
        output = ev.data.summary;
        status = "max_steps_reached";
        break;
      }
      case "cancelled":
        status = "cancelled";
        break;
      case "error": {
        const st = ensure(ev.step);
        st.error = ev.data.message;
        error = ev.data.message;
        status = "error";
        break;
      }
      case "finished":
        // The authoritative terminal status (str(RunStatus)).
        status = (ev.data.status as RunStatus) ?? status;
        break;
    }
  }

  const steps = [...map.values()]
    .filter((s) => s.step >= 0)
    .sort((a, b) => a.step - b.step);
  return { task, status, tier, steps, output, error };
}

// ----- persisted Step reduction (the terminal-final snapshot shape) -----

interface PersistedToolCall {
  name: string;
  call_id?: string;
  args?: Record<string, unknown>;
}
interface PersistedToolResult {
  tool_name: string;
  content: string;
  call_id?: string;
  is_error?: boolean;
}
interface PersistedStep {
  type: string;
  tool_calls?: PersistedToolCall[];
  results?: PersistedToolResult[];
  question?: string | null;
  user_answer?: string | null;
  content?: string | null;
  tier_used?: string | null;
}

function isRunEventDict(x: unknown): x is RunEvent {
  return typeof x === "object" && x !== null && "timestamp" in x;
}

function stepToRunStep(s: PersistedStep, index: number): RunStep {
  const out = emptyStep(index);
  out.tier = s.tier_used ?? undefined;
  switch (s.type) {
    case "tool_call": {
      const calls = s.tool_calls ?? [];
      const results = s.results ?? [];
      out.tools = calls.map((c) => {
        const r =
          (c.call_id
            ? results.find((x) => x.call_id && x.call_id === c.call_id)
            : undefined) ?? results.find((x) => x.tool_name === c.name);
        return {
          toolName: c.name,
          args: c.args,
          result: r?.content,
          isError: r?.is_error,
          pending: r === undefined,
        };
      });
      break;
    }
    case "ask_user":
      out.question = s.question ?? undefined;
      out.answered = s.user_answer != null;
      break;
    case "final":
      out.final = s.content ?? undefined;
      break;
    case "reasoning":
      out.reasoning = s.content ?? undefined;
      break;
    case "error":
      out.error = s.content ?? undefined;
      break;
  }
  return out;
}

/**
 * Build a {@link RunView} from a `GET /runs/:id` response, handling both
 * `steps[]` shapes. The response's top-level `status`/`output`/`error` are
 * authoritative over anything derived from the steps.
 */
export function runViewFromSnapshot(snap: RunStatusResponse): RunView {
  const raw = snap.steps ?? [];
  const status = snap.status as RunStatus;

  if (raw.length > 0 && isRunEventDict(raw[0])) {
    const view = runViewFromEvents(raw as unknown as RunEvent[], {
      task: snap.task,
    });
    return {
      ...view,
      status,
      output: snap.output ?? view.output,
      error: snap.error ?? view.error,
    };
  }

  const steps = (raw as unknown as PersistedStep[]).map(stepToRunStep);
  return {
    task: snap.task,
    status,
    tier: steps.find((s) => s.tier)?.tier,
    steps,
    output: snap.output ?? undefined,
    error: snap.error ?? undefined,
  };
}
