"use client";

/**
 * Spec F2 T17 — StreamingTextRenderer.
 *
 * Implements D-F2-5 mechanism B as resolved (Phase 3 measured-locked):
 *   useTransition + rAF-coalesced append.
 *
 * The X-F2-1 measurement (2026-06-05) showed B's input-lag max stays within
 * the one-frame budget (17.4ms vs A's 26.2ms outlier) at DeepSeek's
 * empirical cadence (~33-60 chunks/sec). C (mutable text-node) remains the
 * documented escape hatch with the same prop-shape if future profiling at
 * faster cadences or larger messages shows B insufficient — swap is a
 * one-component change. See docs/specs/phase2/spec_F2/measurements/.
 *
 * D-F2-12 (provisional): vermilion `--primary` caret. Mechanism-orthogonal
 * (the X-F2-1 measurement confirmed it renders smoothly under all three
 * mechanisms). The T17 implementing agent MUST NOT bundle this with D-F2-5.
 * Final caret-colour lock at T34 criterion-#11 review.
 *
 * D-F2-13 (lean): reuse F1 `/reference/run` thinking-indicator pattern for
 * the pre-first-token "thinking" state. Three muted-foreground pulse dots
 * with staggered offsets; F1 T15 reduced-motion silences via universal
 * !important on animation-duration.
 *
 * Contract: controlled by the parent (`text` prop is the current accumulated
 * content). Internally, T17 buffers prop changes through rAF and commits the
 * displayed text via `startTransition` so the commit is interruptible by
 * higher-priority work (user typing in the chat composer, scroll, click on
 * a tool-call card). The mechanism B benefit lives inside this component;
 * upstream (`useChat` in spec-09) is unchanged.
 *
 * Edge cases handled (verified by tests):
 *   - text shrink / reset (conversation switch) → sync commit, no transition.
 *   - text equals current displayed → no-op.
 *   - non-streaming terminal text → sync commit (no caret, no transition).
 *   - thinking=true && text empty → ThinkingIndicator, no text yet.
 */

import { useEffect, useRef, useState, useTransition } from "react";
import { cn } from "@/lib/utils";

interface StreamingTextRendererProps {
  /** The current accumulated text. Grows as chunks arrive upstream. */
  text: string;
  /**
   * True while chunks are arriving. Drives the caret + the transition-lane
   * commit. When false, the renderer commits text synchronously and hides
   * the caret.
   */
  streaming?: boolean;
  /**
   * True between request-sent and first-chunk-received. When true AND text
   * is empty, the ThinkingIndicator renders in place of the text body.
   * Transitions to streaming=true when the first chunk lands.
   */
  thinking?: boolean;
  /**
   * Accessible label for the thinking indicator. F2 T25 i18n convention:
   * primitives stay portable by exposing user-facing strings as props;
   * consumers (MessageElement, chat-window) pass `t("chat.thinking")`
   * from their own next-intl scope. English default lets the primitive
   * render coherently without a wrapper.
   */
  thinkingLabel?: string;
  className?: string;
}

export function StreamingTextRenderer({
  text,
  streaming = false,
  thinking = false,
  thinkingLabel = "Thinking",
  className,
}: StreamingTextRendererProps) {
  const [displayed, setDisplayed] = useState(text);
  const [, startTransition] = useTransition();
  const pending = useRef<string>(text);
  const rafId = useRef<number | null>(null);

  useEffect(() => {
    // Reset / shrink: a conversation switch or a re-fetch may pass text
    // shorter than displayed. Commit synchronously — no transition needed
    // because we're not appending; we're replacing.
    if (text.length < displayed.length || !streaming) {
      if (rafId.current !== null) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
      }
      pending.current = text;
      if (text !== displayed) setDisplayed(text);
      return;
    }

    // Same content — no-op.
    if (text === displayed) {
      pending.current = text;
      return;
    }

    // Growth path: buffer the new text in a ref, schedule a single rAF flush
    // that commits via startTransition. If more text arrives before rAF
    // fires, the ref updates but only one transition is dispatched per frame
    // — this is the "rAF-coalesced append" half of mechanism B.
    pending.current = text;
    if (rafId.current === null) {
      rafId.current = requestAnimationFrame(() => {
        const next = pending.current;
        rafId.current = null;
        startTransition(() => setDisplayed(next));
      });
    }
  }, [text, displayed, streaming]);

  // Cleanup pending rAF on unmount so we don't leak callbacks across
  // conversation switches.
  useEffect(() => {
    return () => {
      if (rafId.current !== null) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
      }
    };
  }, []);

  // Pre-first-token thinking state — only when no text has arrived yet.
  if (thinking && !displayed) {
    return <ThinkingIndicator label={thinkingLabel} className={className} />;
  }

  return (
    <output
      className={cn("type-body block whitespace-pre-wrap", className)}
      aria-live="polite"
      data-slot="streaming-text"
      data-streaming={streaming ? "true" : "false"}
    >
      {displayed}
      {streaming ? <Caret /> : null}
    </output>
  );
}

/**
 * Vermilion `--primary` streaming caret (D-F2-12 provisional). The 3px
 * width + 16px height are positional pixels — sub-token sizing for an
 * inline element next to text. Caret is decorative (aria-hidden); the
 * `<output aria-live="polite">` wrapper announces the text itself.
 *
 * Reduced-motion: F1 T15 silences `animate-pulse` via universal !important
 * on animation-duration. The caret stays visible as a steady positional
 * indicator; the pulse stops.
 */
function Caret() {
  return (
    <span
      aria-hidden="true"
      className="ml-0.5 inline-block h-4 w-[3px] translate-y-0.5 animate-pulse rounded-full bg-primary"
      data-slot="streaming-caret"
    />
  );
}

/**
 * Pre-first-token thinking indicator. F1 `/reference/run` pattern adapted
 * for inline-in-message context. Three softly-pulsing dots paired with a
 * visible italic label — the dots provide the visual "activity" cue, the
 * label tells the reader who's thinking and why the message is paused.
 *
 * Earlier iteration was dots-only with the label hidden in `aria-label`
 * (visually accessible only to screen readers). User feedback 2026-06-06:
 * dots-only reads as "weird stray markers," not "the persona is thinking."
 * The visible label + `py-1.5` vertical breathing room + slightly larger
 * (`size-2`) dots + a longer pulse stagger (0/200/400ms — a more readable
 * wave) make the indicator read as a natural conversation pause.
 *
 * Token consumption: `text-muted-foreground` for the label + dot fill
 * (with `/70` opacity for a softer "still loading" feel); `type-ui` for
 * the label's inline-text size (matches F1's body-text role rather than
 * the badge-style `.type-caption`).
 */
function ThinkingIndicator({
  label,
  className,
}: {
  label: string;
  className?: string;
}) {
  return (
    <output
      aria-label={label}
      className={cn(
        "type-ui inline-flex items-center gap-2 py-1.5 text-muted-foreground italic",
        className,
      )}
      data-slot="streaming-thinking"
    >
      <span aria-hidden="true" className="inline-flex items-center gap-1">
        <span
          className="size-2 animate-pulse rounded-full bg-muted-foreground/70"
          style={{ animationDelay: "0ms" }}
        />
        <span
          className="size-2 animate-pulse rounded-full bg-muted-foreground/70"
          style={{ animationDelay: "200ms" }}
        />
        <span
          className="size-2 animate-pulse rounded-full bg-muted-foreground/70"
          style={{ animationDelay: "400ms" }}
        />
      </span>
      <span>{label}</span>
    </output>
  );
}
