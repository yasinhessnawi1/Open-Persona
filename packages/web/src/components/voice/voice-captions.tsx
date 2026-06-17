"use client";

/**
 * Spec V6 C1 — live captions (the D-V6-2 accessibility floor).
 *
 * The dual-region split that resolves the live-visual-vs-sane-screen-reader
 * tension:
 *
 *   - **Visual caption** (sighted / hard-of-hearing): a SCROLLABLE transcript of
 *     the conversation, attributed by speaker. It auto-scrolls to the newest
 *     line, but the moment the user scrolls up (to re-read what the persona
 *     asked, say), it stops yanking them down — auto-scroll resumes only when
 *     they return to the bottom. It is NOT an ARIA live region (partials must
 *     never be announced). React keys by `segmentId`, so a partial
 *     mutate-and-replaces its segment in place and a finalized line never reflows.
 *   - **Screen-reader region**: a separate `role="log"` (implicit
 *     `aria-live="polite"`, `aria-atomic="false"`) into which ONLY finalized
 *     segments are appended — one complete attributed utterance at a time.
 *
 * Persona captions are verbatim from the TTS source; the user side is ASR.
 * Speaker attribution is explicit (deaf/HoH users can't infer it).
 */

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { Markdown } from "@/components/ui/markdown";
import { type CaptionSegment, finalisedCaptions } from "@/lib/voice/captions";

export interface VoiceCaptionsProps {
  captions: CaptionSegment[];
  personaName: string;
}

export function VoiceCaptions({
  captions,
  personaName,
}: VoiceCaptionsProps): React.JSX.Element | null {
  const t = useTranslations("voice");
  const scrollRef = useRef<HTMLDivElement>(null);
  // Pinned = follow the newest line. Unpinned = the user scrolled up to read
  // back; respect their position until they return to the bottom themselves.
  const [pinned, setPinned] = useState(true);

  // Auto-scroll on new/updated caption text — only while pinned to the bottom.
  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll on every caption mutation
  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinned) el.scrollTop = el.scrollHeight;
  }, [captions, pinned]);

  if (captions.length === 0) return null;

  const speakerLabel = (speaker: CaptionSegment["speaker"]): string =>
    speaker === "user" ? t("you") : personaName;
  const finals = finalisedCaptions(captions);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    // A small tolerance so sub-pixel rounding at the bottom doesn't unpin.
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    setPinned(atBottom);
  };

  return (
    <div className="w-full max-w-2xl">
      {/* Visual caption — scrollable transcript; intentionally NOT a live region. */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        aria-hidden
        className="max-h-44 space-y-2 overflow-y-auto rounded-lg bg-black/65 px-4 py-3 text-left text-sm text-white sm:max-h-60"
      >
        {captions.map((seg) => {
          // Render the persona's finalized text as Markdown — same renderer as
          // the chat thread (the model emits **bold**/lists/emoji). Partials and
          // the ASR user side stay plain text (avoid half-typed `**` flicker,
          // mirroring the chat's stream-then-Markdown pattern).
          const asMarkdown = seg.speaker === "persona" && seg.isFinal;
          return (
            <div
              key={seg.segmentId}
              className={seg.isFinal ? undefined : "opacity-80"}
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
