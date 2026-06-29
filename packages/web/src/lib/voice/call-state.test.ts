import { describe, expect, it } from "vitest";
import {
  applyActivity,
  applyVoiceStateEvent,
  callErrorForMediaError,
  callErrorForTokenStatus,
  callPhaseForConnectionState,
  INITIAL_CALL_STATE,
  mergeArtifacts,
  type VoiceActivity,
} from "./call-state";
import type {
  VoiceActivityEndEvent,
  VoiceActivityStartEvent,
  VoiceArtifact,
  VoiceStateEvent,
  VoiceToolResultEvent,
} from "./voice-events";

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

const artifact = (workspacePath: string): VoiceArtifact => ({
  workspacePath,
  mimeType: "image/png",
  sizeBytes: 1,
  renderedInline: false,
});

const toolResult = (...artifacts: VoiceArtifact[]): VoiceToolResultEvent => ({
  type: "tool_result",
  toolName: "generate_image",
  isError: false,
  artifacts,
});

describe("mergeArtifacts — rich-output collection (V10-D-6)", () => {
  it("appends new artifacts in arrival order", () => {
    const after = mergeArtifacts(
      [artifact("a/1.png")],
      toolResult(artifact("a/2.png")),
    );
    expect(after.map((a) => a.workspacePath)).toEqual(["a/1.png", "a/2.png"]);
  });

  it("dedupes by workspacePath and returns the same reference on a no-op", () => {
    const list = [artifact("a/1.png")];
    const after = mergeArtifacts(list, toolResult(artifact("a/1.png")));
    expect(after).toBe(list); // identity preserved → no re-render
  });

  it("adds nothing for an empty-artifact result (e.g. web_search)", () => {
    const list = [artifact("a/1.png")];
    expect(mergeArtifacts(list, toolResult())).toBe(list);
  });
});

const startEvent = (
  activityId: string,
  label: string,
): VoiceActivityStartEvent => ({
  type: "activity_start",
  activityId,
  kind: "tool",
  name: "generate_image",
  label,
});

const endEvent = (activityId: string): VoiceActivityEndEvent => ({
  type: "activity_end",
  activityId,
  status: "ok",
  isError: false,
});

describe("applyActivity — live activity tracking (V10-D-6)", () => {
  it("appends a started activity with its label", () => {
    const after = applyActivity([], startEvent("x1", "Creating an image"));
    expect(after).toEqual<VoiceActivity[]>([
      { activityId: "x1", label: "Creating an image" },
    ]);
  });

  it("removes the matching activity on end", () => {
    let list = applyActivity([], startEvent("x1", "Creating an image"));
    list = applyActivity(list, startEvent("x2", "Searching the web"));
    const after = applyActivity(list, endEvent("x1"));
    expect(after.map((a) => a.activityId)).toEqual(["x2"]);
  });

  it("ignores a duplicate start and an unmatched end (same reference)", () => {
    const list = applyActivity([], startEvent("x1", "Creating an image"));
    expect(applyActivity(list, startEvent("x1", "Creating an image"))).toBe(
      list,
    );
    expect(applyActivity(list, endEvent("nope"))).toBe(list);
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
