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

/** The four V4 conversational states the agent broadcasts (`to_state`). */
export type ConversationalStateName =
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

export type VoiceEvent = VoiceStateEvent | VoiceTranscriptEvent;

const STATE_NAMES = new Set<string>([
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
  if (state === "processing") return "thinking";
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

  return null;
}
