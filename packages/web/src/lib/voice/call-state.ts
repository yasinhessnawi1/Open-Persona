/**
 * Spec V6 A3 — pure call-state helpers (the unit-tested core of useVoiceCall).
 *
 * The WebRTC hook's stateful glue lives in `use-voice-call.ts`; the pure
 * mapping logic lives here so it is testable without a real LiveKit Room
 * (criterion 12 — "Vitest covers the client state handling"). The Playwright
 * operator pass exercises the live wiring.
 */

import type { AgentVisualState } from "./voice-events";

/**
 * The user-facing call phase (D-V6-5 honest states). `connecting` covers token
 * fetch + signaling; `reconnecting` is a transient drop the SDK is recovering;
 * `dropped` is a non-recovered loss; `ended` is a clean hang-up; `error` is a
 * pre-connect failure (mic denied, token 4xx).
 */
export type CallPhase =
  | "idle"
  | "connecting"
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
  needsAudioGesture: false,
  error: null,
};

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
