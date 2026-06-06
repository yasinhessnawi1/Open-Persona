/**
 * Spec F2 T23 — Transition primitives.
 *
 * Lightweight CSS-only entrance transitions that consume F1's motion-duration
 * and motion-ease tokens. Used wherever a component needs a fade or slide-in
 * beyond what shadcn primitives + sonner provide (e.g., toast-result panels,
 * expand-on-mount content).
 *
 * Server components (D-F2-3) — CSS-driven; no React state. Reduced-motion
 * silenced structurally by F1 T15 (universal !important on
 * transition-duration).
 *
 * Both primitives use the `tw-animate-css` package the scaffold already
 * ships. Tailwind v4 exposes the animate-in, fade-in, and directional
 * slide-in-from utilities; we layer F1 motion tokens via arbitrary-value
 * `duration` overrides keyed to fast / normal / slow.
 *
 * Note: literal Tailwind class names with a glob (the `*` wildcard) are
 * deliberately NOT written in these JSDoc blocks. Tailwind v4's content
 * scanner picks up class-shaped strings even inside comments and would
 * generate an invalid CSS class with a literal `*` inside `var()`.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface TransitionProps {
  children: ReactNode;
  className?: string;
  /** Duration token. `fast` = 120ms, `normal` = 200ms (default), `slow` = 320ms. */
  speed?: "fast" | "normal" | "slow";
}

const DURATION_CLASS: Record<NonNullable<TransitionProps["speed"]>, string> = {
  fast: "duration-[var(--motion-duration-fast)]",
  normal: "duration-[var(--motion-duration-normal)]",
  slow: "duration-[var(--motion-duration-slow)]",
};

/**
 * Fade entrance — opacity 0 → 1 over the chosen duration with the F1
 * standard easing. Use for content that lands without directional motion
 * (e.g., a panel revealing on tab change).
 */
export function FadeTransition({
  children,
  className,
  speed = "normal",
}: TransitionProps) {
  return (
    <div
      className={cn(
        "animate-in fade-in ease-[var(--motion-ease-standard)]",
        DURATION_CLASS[speed],
        className,
      )}
      data-slot="fade-transition"
    >
      {children}
    </div>
  );
}

/**
 * Slide entrance — translates in from the given side over the chosen
 * duration with the F1 emphasized easing (matches sheet drawer feel).
 * Use for content that enters from a clear directional origin (e.g., a
 * dropdown reveal, a notification slide-in if sonner isn't involved).
 */
interface SlideTransitionProps extends TransitionProps {
  /** Slide origin. Default `top` (slide-down into view). */
  from?: "top" | "bottom" | "left" | "right";
}

const SLIDE_FROM: Record<NonNullable<SlideTransitionProps["from"]>, string> = {
  top: "slide-in-from-top-2",
  bottom: "slide-in-from-bottom-2",
  left: "slide-in-from-left-2",
  right: "slide-in-from-right-2",
};

export function SlideTransition({
  children,
  className,
  speed = "normal",
  from = "top",
}: SlideTransitionProps) {
  return (
    <div
      className={cn(
        "animate-in fade-in ease-[var(--motion-ease-emphasized)]",
        SLIDE_FROM[from],
        DURATION_CLASS[speed],
        className,
      )}
      data-slot="slide-transition"
      data-from={from}
    >
      {children}
    </div>
  );
}
