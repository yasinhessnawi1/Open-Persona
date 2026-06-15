import { describe, expect, it } from "vitest";
import {
  callErrorForMediaError,
  callErrorForTokenStatus,
  callPhaseForConnectionState,
} from "./call-state";

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
