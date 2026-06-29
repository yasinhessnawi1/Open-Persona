/**
 * Spec V6 A3 — pure call-state helpers (the unit-tested core of useVoiceCall).
 *
 * The WebRTC hook's stateful glue lives in `use-voice-call.ts`; the pure
 * mapping logic lives here so it is testable without a real LiveKit Room
 * (criterion 12 — "Vitest covers the client state handling"). The Playwright
 * operator pass exercises the live wiring.
 */

import {
  type AgentVisualState,
  agentVisualState,
  isBargeIn,
  type VoiceActivityEndEvent,
  type VoiceActivityStartEvent,
  type VoiceArtifact,
  type VoiceStateEvent,
  type VoiceToolResultEvent,
} from "./voice-events";

/**
 * The user-facing call phase (D-V6-5 honest states). `connecting` covers token
 * fetch + signaling; `ringing` is the Spec 32 greet-first opening (the persona
 * is preparing its greeting, mic gated); `reconnecting` is a transient drop the
 * SDK is recovering; `dropped` is a non-recovered loss; `ended` is a clean
 * hang-up; `error` is a pre-connect failure (mic denied, token 4xx).
 */
export type CallPhase =
  | "idle"
  | "connecting"
  | "ringing"
  | "connected"
  | "reconnecting"
  | "dropped"
  | "ended"
  | "error";

/** The live call state the call surface renders. */
export interface VoiceCallState {
  phase: CallPhase;
  /** The persona-side cue the orb renders (D-V6-1). */
  agentState: AgentVisualState;
  /**
   * A monotonically-increasing counter bumped on each confirmed barge-in, so the
   * orb can fire its visible-yield animation off a real V4 transition (D-V6-1,
   * criterion 4). The value itself is opaque; a change is the signal.
   */
  bargeInSignal: number;
  /** Whether the user's mic is publishing (mute toggles this). */
  micActive: boolean;
  /**
   * Spec 32 C3 — while true, the mic is held gated for the greet-first opening
   * (the persona speaks turn 0 first). Set when the call starts ringing; cleared
   * (un-gating the mic) exactly when the greeting finishes. After that, `micActive`
   * is the user's mute control and this stays false.
   */
  micGatedForGreeting: boolean;
  /** Autoplay blocked the persona audio — surface a "tap to enable audio" affordance. */
  needsAudioGesture: boolean;
  /** A pre-connect / fatal error to surface honestly (D-V6-5). */
  error: VoiceCallError | null;
}

export interface VoiceCallError {
  /** A stable kind for branching the UI copy. */
  kind:
    | "mic_denied"
    | "mic_missing"
    | "mic_busy"
    | "unauthorized"
    | "credits_exhausted"
    | "not_found"
    | "service_unavailable"
    | "unknown";
  message: string;
}

export const INITIAL_CALL_STATE: VoiceCallState = {
  phase: "idle",
  agentState: "listening",
  bargeInSignal: 0,
  micActive: false,
  micGatedForGreeting: false,
  needsAudioGesture: false,
  error: null,
};

/**
 * Fold a decoded conversational-state event into the call state — the Spec 32
 * greet-first ring lifecycle plus the existing barge-in / orb cue mapping (the
 * pure core the hook applies; unit-tested without a real Room).
 *
 * The ring lifecycle:
 *   - `preparing`      → ring + gate the mic (the persona is preparing turn 0);
 *   - `persona_speaking` while gated → stop ringing, play the greeting (mic stays
 *     gated until it finishes — the MUTE_UNTIL_FIRST_BOT_COMPLETE contract);
 *   - first `listening` while gated  → the greeting finished → un-gate the mic.
 * After the gate is spent, only the orb cue (and the barge-in signal) update; the
 * mic is the user's mute control.
 */
export function applyVoiceStateEvent(
  state: VoiceCallState,
  event: VoiceStateEvent,
): VoiceCallState {
  const agentState = agentVisualState(event.toState);

  if (event.toState === "preparing") {
    return {
      ...state,
      phase: "ringing",
      agentState,
      micActive: false,
      micGatedForGreeting: true,
    };
  }

  if (state.micGatedForGreeting) {
    if (event.toState === "persona_speaking") {
      // The greeting is starting — stop ringing, but keep the mic gated until it
      // finishes so the greeting and the user's first words never collide.
      return { ...state, phase: "connected", agentState };
    }
    if (event.toState === "listening") {
      // Greeting finished → un-gate the mic exactly here (un-gate at completion,
      // never at the greeting's start). Barge-in is normal from now on.
      return {
        ...state,
        phase: "connected",
        agentState,
        micActive: true,
        micGatedForGreeting: false,
      };
    }
  }

  if (isBargeIn(event)) {
    // Reflect the REAL V4 barge-in (criterion 4) — bump the signal, never compute.
    return { ...state, agentState, bargeInSignal: state.bargeInSignal + 1 };
  }

  return { ...state, agentState };
}

/**
 * Map a LiveKit `ConnectionState` (+ whether the disconnect was client-initiated)
 * onto our {@link CallPhase}. Kept as a string-keyed map so it never imports the
 * SDK enum (the hook passes the enum's string value through).
 */
export function callPhaseForConnectionState(
  connectionState: string,
  opts: { clientInitiated: boolean } = { clientInitiated: false },
): CallPhase {
  switch (connectionState) {
    case "connecting":
      return "connecting";
    case "connected":
      return "connected";
    case "reconnecting":
    case "signalReconnecting":
      return "reconnecting";
    case "disconnected":
      return opts.clientInitiated ? "ended" : "dropped";
    default:
      return "idle";
  }
}

/**
 * One in-flight capability the call surface renders a "using <X>…" badge for
 * (V10-D-6). Kept minimal — only the fields the badge reads — and tracked by
 * `activityId` so the paired `activity_end` clears exactly its own badge.
 */
export interface VoiceActivity {
  activityId: string;
  label: string;
}

/**
 * Fold a `tool_result`'s artifacts into the accumulated list, deduped by
 * `workspacePath` (V10-D-6) — a tool may emit the same artifact across an
 * in-turn + render-when-ready pair, and a re-render must not double-mount it.
 * Empty-artifact results (e.g. `web_search`) add nothing. Pure: returns the same
 * reference when there is nothing new, so a no-op event triggers no re-render.
 */
export function mergeArtifacts(
  list: VoiceArtifact[],
  event: VoiceToolResultEvent,
): VoiceArtifact[] {
  if (event.artifacts.length === 0) return list;
  const seen = new Set(list.map((a) => a.workspacePath));
  const additions = event.artifacts.filter((a) => !seen.has(a.workspacePath));
  return additions.length === 0 ? list : [...list, ...additions];
}

/**
 * Apply one activity frame to the active-activity list (V10-D-6): `start` appends
 * (ignoring a duplicate `activityId`), `end` removes the matching one. Pure;
 * returns the same reference on a no-op so an unmatched end never re-renders.
 */
export function applyActivity(
  list: VoiceActivity[],
  event: VoiceActivityStartEvent | VoiceActivityEndEvent,
): VoiceActivity[] {
  if (event.type === "activity_start") {
    if (list.some((a) => a.activityId === event.activityId)) return list;
    return [...list, { activityId: event.activityId, label: event.label }];
  }
  const next = list.filter((a) => a.activityId !== event.activityId);
  return next.length === list.length ? list : next;
}

/** Map a LiveKit `getUserMedia`/`MediaDevicesError` onto a typed call error (D-V6-5). */
export function callErrorForMediaError(err: unknown): VoiceCallError {
  const name =
    typeof err === "object" && err !== null && "name" in err
      ? String((err as { name: unknown }).name)
      : "";
  switch (name) {
    case "NotAllowedError":
      // Browser-deny AND OS-level block are indistinguishable from the error —
      // one honest affordance covers both (D-V6-5).
      return {
        kind: "mic_denied",
        message:
          "Microphone access is blocked. Enable it in your browser or system settings.",
      };
    case "NotFoundError":
      return { kind: "mic_missing", message: "No microphone was found." };
    case "NotReadableError":
      return {
        kind: "mic_busy",
        message: "Your microphone is in use by another app.",
      };
    default:
      return {
        kind: "unknown",
        message: "Could not start the call. Please try again.",
      };
  }
}

/** Map a token-endpoint HTTP status onto a typed call error (the fail-closed contract). */
export function callErrorForTokenStatus(status: number): VoiceCallError {
  switch (status) {
    case 401:
      return {
        kind: "unauthorized",
        message: "Your session expired. Please sign in again.",
      };
    case 402:
      return {
        kind: "credits_exhausted",
        message: "You're out of voice credits.",
      };
    case 404:
      return { kind: "not_found", message: "This persona isn't available." };
    default:
      return {
        kind: "service_unavailable",
        message:
          "The voice service is unavailable right now. Please try again.",
      };
  }
}
