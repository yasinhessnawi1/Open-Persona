"use client";

/**
 * Spec V6 C1 — live captions (the D-V6-2 accessibility floor).
 *
 * The dual-region split that resolves the live-visual-vs-sane-screen-reader
 * tension:
 *
 *   - **Visual caption** (sighted / hard-of-hearing): a bottom scrim showing the
 *     live tail (current + previous segment), attributed by speaker. It is NOT
 *     an ARIA live region — partials must never be announced. React keys by
 *     `segmentId`, so a partial mutate-and-replaces its segment in place and a
 *     finalized line is never reflowed (its id never receives another partial).
 *   - **Screen-reader region**: a separate `role="log"` (implicit
 *     `aria-live="polite"`, `aria-atomic="false"`) into which ONLY finalized
 *     segments are appended — one complete attributed utterance at a time, never
 *     a partial, never the whole history re-announced.
 *
 * Persona captions are verbatim from the TTS source (perfect); the user side is
 * ASR. Speaker attribution is explicit (deaf/HoH users can't infer it).
 */

import { useTranslations } from "next-intl";
import { Markdown } from "@/components/ui/markdown";
import {
  type CaptionSegment,
  captionTail,
  finalisedCaptions,
} from "@/lib/voice/captions";

export interface VoiceCaptionsProps {
  captions: CaptionSegment[];
  personaName: string;
}

export function VoiceCaptions({
  captions,
  personaName,
}: VoiceCaptionsProps): React.JSX.Element | null {
  const t = useTranslations("voice");
  if (captions.length === 0) return null;

  const speakerLabel = (speaker: CaptionSegment["speaker"]): string =>
    speaker === "user" ? t("you") : personaName;

  const tail = captionTail(captions, 2);
  const finals = finalisedCaptions(captions);

  return (
    <div className="w-full max-w-xl">
      {/* Visual caption — intentionally NOT a live region (no aria-live). */}
      <div
        aria-hidden
        className="space-y-1 rounded-lg bg-black/65 px-4 py-2 text-left text-sm text-white"
      >
        {tail.map((seg) => {
          // Render the persona's finalized text as Markdown — same renderer as
          // the chat thread (the model emits **bold**/lists/emoji). Partials and
          // the ASR user side stay plain text (avoid half-typed `**` flicker,
          // mirroring the chat's stream-then-Markdown pattern).
          const asMarkdown = seg.speaker === "persona" && seg.isFinal;
          return (
            <div
              key={seg.segmentId}
              className={seg.isFinal ? undefined : "opacity-75"}
            >
              <span className="font-medium">{speakerLabel(seg.speaker)}:</span>{" "}
              {asMarkdown ? (
                <Markdown>{seg.text}</Markdown>
              ) : (
                <span>{seg.text}</span>
              )}
            </div>
          );
        })}
      </div>

      {/* Screen-reader region — finals only, polite, append-only. */}
      <div role="log" aria-label={t("captions")} className="sr-only">
        {finals.map((seg) => (
          <p key={seg.segmentId}>
            {speakerLabel(seg.speaker)}: {seg.text}
          </p>
        ))}
      </div>
    </div>
  );
}
