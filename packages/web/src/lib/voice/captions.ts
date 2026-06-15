/**
 * Spec V6 C1 — the caption-segment model (pure; the unit-tested core of D-V6-2).
 *
 * Transcript frames arrive over the data channel (decoded by `voice-events.ts`)
 * as `{ speaker, text, isFinal, segmentId }`. Partials of one utterance share a
 * `segmentId`; a new utterance gets a fresh id after the final. This reducer
 * upserts by id — so a partial mutate-and-replaces the current segment in place
 * (the entire anti-flicker strategy) and finalized segments are never reflowed
 * (their id never receives another partial).
 *
 * The rendering split (D-V6-2) lives in the component: the visual caption shows
 * the live tail (current + recent), NOT as an ARIA live region; a separate
 * `role="log"` polite region announces ONLY finalized segments to screen readers.
 */

import type { VoiceTranscriptEvent } from "./voice-events";

export interface CaptionSegment {
  segmentId: string;
  speaker: "user" | "persona";
  text: string;
  isFinal: boolean;
}

/** Keep the model bounded — captions are a live tail, not a full history here. */
export const MAX_CAPTION_SEGMENTS = 50;

/**
 * Upsert a transcript frame into the ordered segment list. A frame whose
 * `segmentId` already exists replaces that segment in place (mutate-and-replace);
 * a new id appends. The list is trimmed to {@link MAX_CAPTION_SEGMENTS}.
 */
export function upsertCaption(
  segments: readonly CaptionSegment[],
  event: VoiceTranscriptEvent,
): CaptionSegment[] {
  const segment: CaptionSegment = {
    segmentId: event.segmentId,
    speaker: event.speaker,
    text: event.text,
    isFinal: event.isFinal,
  };
  const index = segments.findIndex((s) => s.segmentId === event.segmentId);
  let next: CaptionSegment[];
  if (index >= 0) {
    next = segments.slice();
    next[index] = segment;
  } else {
    next = [...segments, segment];
  }
  return next.length > MAX_CAPTION_SEGMENTS
    ? next.slice(next.length - MAX_CAPTION_SEGMENTS)
    : next;
}

/** The finalized segments only — what the screen-reader `role="log"` announces. */
export function finalisedCaptions(
  segments: readonly CaptionSegment[],
): CaptionSegment[] {
  return segments.filter((s) => s.isFinal);
}

/** The live tail the visual caption bar renders (last `count` segments). */
export function captionTail(
  segments: readonly CaptionSegment[],
  count = 2,
): CaptionSegment[] {
  return segments.slice(Math.max(0, segments.length - count));
}
