/**
 * Spec V6 A4 — voice data-channel event decoder.
 *
 * The client half of D-V6-E1/E2: one discriminated JSON envelope, reliable +
 * ordered, decoded into typed client events. Hand-mirrored from the backend
 * serializer (the A1 `DataChannelBroadcaster`), the way `sse-types.ts` is
 * mirrored from the API's Pydantic — keep in sync with:
 *   packages/voice/src/persona_voice/transport/broadcast.py (encode_* / topic)
 *
 * Two frame types under a `type` discriminator:
 *   - state:      {type:"state", from_state, to_state, trigger, at}
 *   - transcript: {type:"transcript", speaker, text, is_final, segment_id}
 *
 * The wire is our own trusted service (room-scoped, owner-only — see the
 * broadcaster), so we narrow by the `type` discriminator and map snake→camel
 * rather than deep-validating.
 */

/**
 * The conversational states the agent broadcasts (`to_state`). `preparing` is
 * the Spec 32 greet-first opening — the persona is generating turn 0 while the
 * call rings and the mic is gated.
 */
export type ConversationalStateName =
  | "preparing"
  | "listening"
  | "user_speaking"
  | "processing"
  | "persona_speaking";

/**
 * What the PERSONA is doing — the three ambient cues the orb renders (D-V6-1).
 * Derived from the conversational state: while the user has the floor (listening
 * OR user_speaking) the persona is *listening*; processing is *thinking*;
 * persona_speaking is *speaking*.
 */
export type AgentVisualState = "listening" | "thinking" | "speaking";

/** The barge-in trigger — the persona yielded because the user cut in (D-V6-1). */
export const BARGE_IN_TRIGGER = "barge_in";

/** Greet-first opening signal — the agent has joined and is preparing turn 0 (Spec 32 A4). */
export const GREETING_STARTED_TRIGGER = "greeting_started";

export interface VoiceStateEvent {
  type: "state";
  fromState: ConversationalStateName;
  toState: ConversationalStateName;
  /** The V4 transition trigger (e.g. `barge_in`, `turn_ended`, `model_first_audio`). */
  trigger: string;
  /** ISO-8601 UTC instant the transition fired. */
  at: string;
}

export interface VoiceTranscriptEvent {
  type: "transcript";
  speaker: "user" | "persona";
  text: string;
  isFinal: boolean;
  /** Stable id of the caption segment — the mutate-and-replace target (D-V6-2). */
  segmentId: string;
}

/**
 * One produced artifact carried on a {@link VoiceToolResultEvent} — the SAME
 * shape chat's `ArtifactRef` carries (V10-D-6), camel-cased: it feeds the EXACT
 * `FileRendererPanel` via the file-renderer context (`{workspacePath, mediaType,
 * name}`). `mimeType` is the renderer discriminator.
 */
export interface VoiceArtifact {
  workspacePath: string;
  mimeType: string;
  sizeBytes: number;
  renderedInline: boolean;
}

/**
 * A tool finished during the call (V10-D-6) — the RENDER frame. Carries the
 * produced artifacts so the call surface mounts them in the preview panel
 * (render-when-ready for async tools, in-turn for inline ones like
 * `render_diagram`). Empty `artifacts` (e.g. web_search) renders no panel.
 */
export interface VoiceToolResultEvent {
  type: "tool_result";
  toolName: string;
  isError: boolean;
  artifacts: VoiceArtifact[];
}

/**
 * The persona started using a capability (V10-D-6) — drives the live "using
 * <X>…" badge in the call surface (P2's unified activity contract). Paired with
 * a {@link VoiceActivityEndEvent} by `activityId`.
 */
export interface VoiceActivityStartEvent {
  type: "activity_start";
  activityId: string;
  kind: string;
  name: string;
  label: string;
}

/** A capability finished/failed (V10-D-6) — clears its "using <X>…" badge. */
export interface VoiceActivityEndEvent {
  type: "activity_end";
  activityId: string;
  status: string;
  isError: boolean;
}

export type VoiceEvent =
  | VoiceStateEvent
  | VoiceTranscriptEvent
  | VoiceToolResultEvent
  | VoiceActivityStartEvent
  | VoiceActivityEndEvent;

const STATE_NAMES = new Set<string>([
  "preparing",
  "listening",
  "user_speaking",
  "processing",
  "persona_speaking",
]);

/**
 * Map a conversational state onto the persona-side visual cue the orb renders.
 * `user_speaking` collapses to `listening` — the persona is attending, not
 * speaking, while the user holds the floor.
 */
export function agentVisualState(
  state: ConversationalStateName,
): AgentVisualState {
  // `preparing` (generating turn 0) is self-driven with no audio yet — the same
  // "thinking" cue as `processing` (mirrors the backend projection, Spec 32).
  if (state === "processing" || state === "preparing") return "thinking";
  if (state === "persona_speaking") return "speaking";
  return "listening";
}

/** Whether a state event is the visible-yield barge-in (persona_speaking → user_speaking). */
export function isBargeIn(event: VoiceStateEvent): boolean {
  return event.trigger === BARGE_IN_TRIGGER;
}

function decode(payload: Uint8Array | string): unknown {
  const text =
    typeof payload === "string" ? payload : new TextDecoder().decode(payload);
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/**
 * Parse one data-channel frame into a typed {@link VoiceEvent}. Returns null for
 * malformed JSON, an unknown `type`, or a frame missing required fields
 * (forward-compatible — an unrecognised frame is ignored, never throws).
 */
export function parseVoiceEvent(
  payload: Uint8Array | string,
): VoiceEvent | null {
  const raw = decode(payload);
  if (typeof raw !== "object" || raw === null) return null;
  const frame = raw as Record<string, unknown>;

  if (frame.type === "state") {
    const from = frame.from_state;
    const to = frame.to_state;
    if (
      typeof from !== "string" ||
      typeof to !== "string" ||
      !STATE_NAMES.has(from) ||
      !STATE_NAMES.has(to) ||
      typeof frame.trigger !== "string" ||
      typeof frame.at !== "string"
    ) {
      return null;
    }
    return {
      type: "state",
      fromState: from as ConversationalStateName,
      toState: to as ConversationalStateName,
      trigger: frame.trigger,
      at: frame.at,
    };
  }

  if (frame.type === "transcript") {
    const speaker = frame.speaker;
    if (
      (speaker !== "user" && speaker !== "persona") ||
      typeof frame.text !== "string" ||
      typeof frame.is_final !== "boolean" ||
      typeof frame.segment_id !== "string"
    ) {
      return null;
    }
    return {
      type: "transcript",
      speaker,
      text: frame.text,
      isFinal: frame.is_final,
      segmentId: frame.segment_id,
    };
  }

  // V10-D-6 rich-output frames. The artifact shape mirrors chat's ArtifactRef so
  // the SAME FileRendererPanel renders it; an absent/garbled artifacts list
  // degrades to an empty list (a tool_result with no artifact renders no panel).
  if (frame.type === "tool_result") {
    if (typeof frame.tool_name !== "string") return null;
    const raw = Array.isArray(frame.artifacts) ? frame.artifacts : [];
    const artifacts = raw
      .map(parseArtifact)
      .filter((a): a is VoiceArtifact => a !== null);
    return {
      type: "tool_result",
      toolName: frame.tool_name,
      isError: Boolean(frame.is_error),
      artifacts,
    };
  }

  if (frame.type === "activity_start") {
    if (
      typeof frame.activity_id !== "string" ||
      typeof frame.kind !== "string" ||
      typeof frame.name !== "string" ||
      typeof frame.label !== "string"
    ) {
      return null;
    }
    return {
      type: "activity_start",
      activityId: frame.activity_id,
      kind: frame.kind,
      name: frame.name,
      label: frame.label,
    };
  }

  if (frame.type === "activity_end") {
    if (
      typeof frame.activity_id !== "string" ||
      typeof frame.status !== "string"
    ) {
      return null;
    }
    return {
      type: "activity_end",
      activityId: frame.activity_id,
      status: frame.status,
      isError: Boolean(frame.is_error),
    };
  }

  return null;
}

/** Map one wire artifact (chat's `ArtifactRef` shape) → {@link VoiceArtifact}. */
function parseArtifact(raw: unknown): VoiceArtifact | null {
  if (typeof raw !== "object" || raw === null) return null;
  const a = raw as Record<string, unknown>;
  if (typeof a.workspace_path !== "string" || typeof a.mime_type !== "string") {
    return null;
  }
  return {
    workspacePath: a.workspace_path,
    mimeType: a.mime_type,
    sizeBytes: typeof a.size_bytes === "number" ? a.size_bytes : 0,
    renderedInline: Boolean(a.rendered_inline),
  };
}
