/**
 * Spec F2 T21 — Loading patterns.
 *
 * The reusable state treatments for "loading something" surfaces. Three
 * skeleton variants + a Spinner. The streaming-text pre-first-token
 * "thinking" indicator lives inside T17 <StreamingTextRenderer> because it's
 * streaming-specific (D-F2-13 lock).
 *
 * Server components (D-F2-3); CSS-driven animation only; reduced-motion
 * silenced structurally by F1 T15's universal !important.
 */

import { cn } from "@/lib/utils";

/**
 * Single-line skeleton — for short text labels. Width defaults to full;
 * pass `w-{n}` via className for fixed widths (e.g., `w-24`).
 */
export function SkeletonLine({ className }: { className?: string }) {
  return (
    <div
      className={cn("h-3 w-full animate-pulse rounded-md bg-muted", className)}
      aria-hidden="true"
      data-slot="skeleton-line"
    />
  );
}

/**
 * Multi-line skeleton block — for paragraphs / card bodies. Renders `lines`
 * skeleton bars with descending widths (the last is shorter to feel like
 * end-of-paragraph). Defaults to 3 lines.
 */
export function SkeletonBlock({
  lines = 3,
  className,
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div
      className={cn("flex flex-col gap-2", className)}
      aria-hidden="true"
      data-slot="skeleton-block"
    >
      {Array.from({ length: lines }).map((_, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: synthetic skeleton bars have no id
          key={i}
          className={cn(
            "h-3 animate-pulse rounded-md bg-muted",
            i === lines - 1 ? "w-2/3" : "w-full",
          )}
        />
      ))}
    </div>
  );
}

/**
 * Circular skeleton — for avatar placeholders. Sizes match <PersonaAvatar>:
 * sm 24px, md 40px, lg 64px.
 */
const SKELETON_AVATAR_SIZE: Record<"sm" | "md" | "lg", string> = {
  sm: "size-6",
  md: "size-10",
  lg: "size-16",
};

export function SkeletonAvatar({
  size = "md",
  className,
}: {
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-full bg-muted",
        SKELETON_AVATAR_SIZE[size],
        className,
      )}
      aria-hidden="true"
      data-slot="skeleton-avatar"
    />
  );
}

/**
 * Spinner — for action-in-progress states (e.g., submit button while
 * the request is in flight). Consumes --motion-duration-slow (320ms) for
 * one rotation; reduced-motion silences via F1 T15.
 *
 * Pass `aria-label` to convey what's loading (the spinner itself is
 * decorative; the label becomes the accessible name).
 */
const SPINNER_SIZE: Record<"sm" | "md" | "lg", string> = {
  sm: "size-3.5",
  md: "size-4",
  lg: "size-5",
};

export function Spinner({
  size = "md",
  className,
  label,
}: {
  size?: "sm" | "md" | "lg";
  className?: string;
  label?: string;
}) {
  return (
    <output
      aria-label={label ?? "Loading"}
      className={cn("inline-block", className)}
      data-slot="spinner"
    >
      <svg
        className={cn(
          "animate-spin text-muted-foreground",
          "duration-[var(--motion-duration-slow)]",
          SPINNER_SIZE[size],
        )}
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <circle
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="3"
          className="opacity-20"
        />
        <path
          d="M22 12a10 10 0 0 1-10 10"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        />
      </svg>
    </output>
  );
}
