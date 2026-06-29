import { describe, expect, it } from "vitest";
import {
  agentVisualState,
  isBargeIn,
  parseVoiceEvent,
  type VoiceStateEvent,
} from "./voice-events";

const enc = (o: unknown) => new TextEncoder().encode(JSON.stringify(o));

describe("parseVoiceEvent", () => {
  it("decodes a state frame from bytes and maps snake→camel", () => {
    const ev = parseVoiceEvent(
      enc({
        type: "state",
        from_state: "processing",
        to_state: "persona_speaking",
        trigger: "model_first_audio",
        at: "2026-06-15T12:00:00+00:00",
      }),
    );
    expect(ev).toEqual({
      type: "state",
      fromState: "processing",
      toState: "persona_speaking",
      trigger: "model_first_audio",
      at: "2026-06-15T12:00:00+00:00",
    });
  });

  it("decodes a transcript frame (partial) with its segment id", () => {
    const ev = parseVoiceEvent(
      enc({
        type: "transcript",
        speaker: "user",
        text: "hel",
        is_final: false,
        segment_id: "u0",
      }),
    );
    expect(ev).toEqual({
      type: "transcript",
      speaker: "user",
      text: "hel",
      isFinal: false,
      segmentId: "u0",
    });
  });

  it("returns null for malformed JSON, unknown type, and missing fields", () => {
    expect(parseVoiceEvent("{not json")).toBeNull();
    expect(parseVoiceEvent(enc({ type: "mystery" }))).toBeNull();
    expect(
      parseVoiceEvent(enc({ type: "state", to_state: "nope" })),
    ).toBeNull();
    expect(
      parseVoiceEvent(enc({ type: "transcript", speaker: "bot", text: "x" })),
    ).toBeNull();
  });

  // V10-D-6: rich-output frames — same artifact shape as chat's ArtifactRef.
  it("decodes a tool_result frame with its artifacts (snake→camel)", () => {
    const ev = parseVoiceEvent(
      enc({
        type: "tool_result",
        tool_name: "generate_image",
        is_error: false,
        content: "image of a castle",
        kind: "imagegen",
        artifacts: [
          {
            workspace_path: "uploads/abc.png",
            mime_type: "image/png",
            size_bytes: 1024,
            rendered_inline: true,
          },
        ],
      }),
    );
    expect(ev).toEqual({
      type: "tool_result",
      toolName: "generate_image",
      isError: false,
      artifacts: [
        {
          workspacePath: "uploads/abc.png",
          mimeType: "image/png",
          sizeBytes: 1024,
          renderedInline: true,
        },
      ],
    });
  });

  it("decodes a tool_result with no artifacts as an empty artifact list", () => {
    const ev = parseVoiceEvent(
      enc({
        type: "tool_result",
        tool_name: "web_search",
        is_error: false,
        content: "results",
      }),
    );
    expect(ev).toEqual({
      type: "tool_result",
      toolName: "web_search",
      isError: false,
      artifacts: [],
    });
  });

  it("decodes activity_start and activity_end (the using-X badge lifecycle)", () => {
    expect(
      parseVoiceEvent(
        enc({
          type: "activity_start",
          activity_id: "a1",
          kind: "imagegen",
          name: "generate_image",
          label: "Creating an image",
          args_summary: { prompt: "a castle" },
        }),
      ),
    ).toEqual({
      type: "activity_start",
      activityId: "a1",
      kind: "imagegen",
      name: "generate_image",
      label: "Creating an image",
    });
    expect(
      parseVoiceEvent(
        enc({
          type: "activity_end",
          activity_id: "a1",
          status: "ok",
          duration_ms: 12,
          is_error: false,
        }),
      ),
    ).toEqual({
      type: "activity_end",
      activityId: "a1",
      status: "ok",
      isError: false,
    });
  });

  it("returns null for rich-output frames missing required fields", () => {
    expect(parseVoiceEvent(enc({ type: "tool_result" }))).toBeNull();
    expect(
      parseVoiceEvent(enc({ type: "activity_start", activity_id: "a" })),
    ).toBeNull();
  });
});

describe("agentVisualState", () => {
  it("collapses the four states onto the three persona cues", () => {
    expect(agentVisualState("listening")).toBe("listening");
    expect(agentVisualState("user_speaking")).toBe("listening");
    expect(agentVisualState("processing")).toBe("thinking");
    expect(agentVisualState("persona_speaking")).toBe("speaking");
  });
});

describe("isBargeIn", () => {
  it("is true only for the persona_speaking→user_speaking barge-in trigger", () => {
    const barge: VoiceStateEvent = {
      type: "state",
      fromState: "persona_speaking",
      toState: "user_speaking",
      trigger: "barge_in",
      at: "t",
    };
    const normal: VoiceStateEvent = { ...barge, trigger: "turn_ended" };
    expect(isBargeIn(barge)).toBe(true);
    expect(isBargeIn(normal)).toBe(false);
  });
});
