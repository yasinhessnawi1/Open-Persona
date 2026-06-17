import { describe, expect, it } from "vitest";
import {
  applyVoiceStateEvent,
  callErrorForMediaError,
  callErrorForTokenStatus,
  callPhaseForConnectionState,
  INITIAL_CALL_STATE,
} from "./call-state";
import type { VoiceStateEvent } from "./voice-events";

const stateEvent = (
  toState: VoiceStateEvent["toState"],
  trigger = "model_first_audio",
  fromState: VoiceStateEvent["fromState"] = "preparing",
): VoiceStateEvent => ({
  type: "state",
  fromState,
  toState,
  trigger,
  at: "2026-06-16T12:00:00+00:00",
});

describe("applyVoiceStateEvent — greet-first ring lifecycle (Spec 32 C)", () => {
  it("rings and gates the mic on the preparing frame", () => {
    const s = applyVoiceStateEvent(
      INITIAL_CALL_STATE,
      stateEvent("preparing", "greeting_started"),
    );
    expect(s.phase).toBe("ringing");
    expect(s.agentState).toBe("thinking");
    expect(s.micActive).toBe(false);
    expect(s.micGatedForGreeting).toBe(true);
  });

  it("stops ringing and plays the greeting, mic still gated", () => {
    let s = applyVoiceStateEvent(
      INITIAL_CALL_STATE,
      stateEvent("preparing", "greeting_started"),
    );
    s = applyVoiceStateEvent(s, stateEvent("persona_speaking"));
    expect(s.phase).toBe("connected");
    expect(s.agentState).toBe("speaking");
    expect(s.micActive).toBe(false); // greeting plays before the mic opens
    expect(s.micGatedForGreeting).toBe(true);
  });

  it("un-gates the mic exactly when the greeting finishes", () => {
    let s = applyVoiceStateEvent(
      INITIAL_CALL_STATE,
      stateEvent("preparing", "greeting_started"),
    );
    s = applyVoiceStateEvent(s, stateEvent("persona_speaking"));
    s = applyVoiceStateEvent(
      s,
      stateEvent("listening", "persona_finished", "persona_speaking"),
    );
    expect(s.agentState).toBe("listening");
    expect(s.micActive).toBe(true); // un-gated at greeting-end
    expect(s.micGatedForGreeting).toBe(false);
  });

  it("does not re-gate or re-open the mic on later listening transitions", () => {
    // After the greeting, a normal turn cycle must not flip the mic via the
    // greeting gate (mute is the user's control thereafter).
    let s = applyVoiceStateEvent(
      INITIAL_CALL_STATE,
      stateEvent("preparing", "greeting_started"),
    );
    s = applyVoiceStateEvent(s, stateEvent("persona_speaking"));
    s = applyVoiceStateEvent(
      s,
      stateEvent("listening", "persona_finished", "persona_speaking"),
    );
    const muted = { ...s, micActive: false };
    const after = applyVoiceStateEvent(
      muted,
      stateEvent("listening", "persona_finished", "persona_speaking"),
    );
    expect(after.micActive).toBe(false); // greeting gate already spent
  });

  it("bumps the barge-in signal on a real barge-in transition", () => {
    const base = { ...INITIAL_CALL_STATE, phase: "connected" as const };
    const s = applyVoiceStateEvent(
      base,
      stateEvent("user_speaking", "barge_in", "persona_speaking"),
    );
    expect(s.bargeInSignal).toBe(1);
    expect(s.agentState).toBe("listening");
  });
});

describe("callPhaseForConnectionState", () => {
  it("maps the SDK connection states onto call phases", () => {
    expect(callPhaseForConnectionState("connecting")).toBe("connecting");
    expect(callPhaseForConnectionState("connected")).toBe("connected");
    expect(callPhaseForConnectionState("reconnecting")).toBe("reconnecting");
    expect(callPhaseForConnectionState("signalReconnecting")).toBe(
      "reconnecting",
    );
  });

  it("distinguishes a clean hang-up from a hard drop on disconnect", () => {
    expect(
      callPhaseForConnectionState("disconnected", { clientInitiated: true }),
    ).toBe("ended");
    expect(
      callPhaseForConnectionState("disconnected", { clientInitiated: false }),
    ).toBe("dropped");
  });
});

describe("callErrorForMediaError", () => {
  it("maps getUserMedia errors onto one honest affordance per class (D-V6-5)", () => {
    expect(callErrorForMediaError({ name: "NotAllowedError" }).kind).toBe(
      "mic_denied",
    );
    expect(callErrorForMediaError({ name: "NotFoundError" }).kind).toBe(
      "mic_missing",
    );
    expect(callErrorForMediaError({ name: "NotReadableError" }).kind).toBe(
      "mic_busy",
    );
    expect(callErrorForMediaError(new Error("weird")).kind).toBe("unknown");
  });
});

describe("callErrorForTokenStatus", () => {
  it("maps the token endpoint's fail-closed statuses", () => {
    expect(callErrorForTokenStatus(401).kind).toBe("unauthorized");
    expect(callErrorForTokenStatus(402).kind).toBe("credits_exhausted");
    expect(callErrorForTokenStatus(404).kind).toBe("not_found");
    expect(callErrorForTokenStatus(503).kind).toBe("service_unavailable");
  });
});
