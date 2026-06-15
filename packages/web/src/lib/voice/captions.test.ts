import { describe, expect, it } from "vitest";
import {
  type CaptionSegment,
  captionTail,
  finalisedCaptions,
  MAX_CAPTION_SEGMENTS,
  upsertCaption,
} from "./captions";
import type { VoiceTranscriptEvent } from "./voice-events";

const frame = (
  segmentId: string,
  speaker: "user" | "persona",
  text: string,
  isFinal: boolean,
): VoiceTranscriptEvent => ({
  type: "transcript",
  segmentId,
  speaker,
  text,
  isFinal,
});

describe("upsertCaption", () => {
  it("mutate-and-replaces a partial in place (same segmentId), then a new id appends", () => {
    let segs: CaptionSegment[] = [];
    segs = upsertCaption(segs, frame("u0", "user", "hel", false));
    segs = upsertCaption(segs, frame("u0", "user", "hello", false));
    segs = upsertCaption(segs, frame("u0", "user", "hello there", true));
    expect(segs).toHaveLength(1);
    expect(segs[0]).toEqual({
      segmentId: "u0",
      speaker: "user",
      text: "hello there",
      isFinal: true,
    });

    segs = upsertCaption(segs, frame("u1", "user", "next", false));
    expect(segs).toHaveLength(2);
    // The finalized u0 line is never reflowed.
    expect(segs[0].text).toBe("hello there");
  });

  it("keeps user and persona segments distinct (different ids)", () => {
    let segs: CaptionSegment[] = [];
    segs = upsertCaption(segs, frame("u0", "user", "hi", true));
    segs = upsertCaption(segs, frame("p0", "persona", "hello", false));
    expect(segs.map((s) => s.speaker)).toEqual(["user", "persona"]);
  });

  it("bounds the list to MAX_CAPTION_SEGMENTS (drops oldest)", () => {
    let segs: CaptionSegment[] = [];
    for (let i = 0; i < MAX_CAPTION_SEGMENTS + 10; i++) {
      segs = upsertCaption(segs, frame(`u${i}`, "user", `t${i}`, true));
    }
    expect(segs).toHaveLength(MAX_CAPTION_SEGMENTS);
    expect(segs[segs.length - 1].segmentId).toBe(
      `u${MAX_CAPTION_SEGMENTS + 9}`,
    );
  });
});

describe("finalisedCaptions", () => {
  it("returns only finalized segments (the SR-region source)", () => {
    const segs: CaptionSegment[] = [
      { segmentId: "u0", speaker: "user", text: "done", isFinal: true },
      { segmentId: "p0", speaker: "persona", text: "typing", isFinal: false },
    ];
    expect(finalisedCaptions(segs).map((s) => s.segmentId)).toEqual(["u0"]);
  });
});

describe("captionTail", () => {
  it("returns the last N segments (the visual live tail)", () => {
    const segs: CaptionSegment[] = [
      { segmentId: "u0", speaker: "user", text: "a", isFinal: true },
      { segmentId: "p0", speaker: "persona", text: "b", isFinal: true },
      { segmentId: "u1", speaker: "user", text: "c", isFinal: false },
    ];
    expect(captionTail(segs, 2).map((s) => s.text)).toEqual(["b", "c"]);
  });
});
