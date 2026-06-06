/**
 * Spec F2 T20 — Layout primitives + responsive grid.
 *
 * Page-structure primitives composed by T19 AppShell + the rebuilt screens
 * (T26-T31). All server components by default per D-F2-3 (presentational
 * only; no hooks/refs). The rebuilt-screen tasks compose these instead of
 * hand-rolling layout CSS.
 *
 * Consumes F1 tokens (Tailwind utilities resolving through @theme inline):
 *   - --font-heading (Fraunces) for PageHeader title;
 *   - --text-heading-* via .type-heading for Section heading;
 *   - --text-ui-* via .type-ui for Subtitle copy;
 *   - --text-display-* via .type-display for hero variant (T26 chat header
 *     does not use this — it uses PersonaIdentityHeader).
 *
 * Spacing follows F1's "Tailwind v4 default scale" (DESIGN.md); breakpoints
 * are the Tailwind defaults per D-F2-11 (sm 640 / md 768 / lg 1024 /
 * xl 1280 / 2xl 1536).
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// <PageHeader> — sticky page header with Fraunces title + actions slot.
// Used at the top of every (app) route's content body.

interface PageHeaderProps {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({
  title,
  subtitle,
  actions,
  className,
}: PageHeaderProps) {
  return (
    <header
      className={cn("mb-6 flex items-end justify-between gap-4", className)}
      data-slot="page-header"
    >
      <div className="min-w-0">
        <h1 className="type-heading truncate" data-slot="page-header-title">
          {title}
        </h1>
        {subtitle ? (
          <p
            className="type-ui mt-1 text-muted-foreground"
            data-slot="page-header-subtitle"
          >
            {subtitle}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="shrink-0" data-slot="page-header-actions">
          {actions}
        </div>
      ) : null}
    </header>
  );
}

// ---------------------------------------------------------------------------
// <PageBody> — content container with max-width + responsive horizontal
// padding. The mx-auto centers within the shell's main area.

interface PageBodyProps {
  children: ReactNode;
  className?: string;
  /**
   * Max width preset. `narrow` = max-w-2xl (chat-like); `default` = max-w-4xl
   * (lists, settings); `wide` = max-w-6xl (data-dense). Per-screen choice.
   */
  width?: "narrow" | "default" | "wide";
}

const WIDTH_CLASSES: Record<NonNullable<PageBodyProps["width"]>, string> = {
  narrow: "max-w-2xl",
  default: "max-w-4xl",
  wide: "max-w-6xl",
};

export function PageBody({
  children,
  className,
  width = "default",
}: PageBodyProps) {
  return (
    <div
      className={cn(
        "mx-auto w-full px-4 py-8 sm:px-6",
        WIDTH_CLASSES[width],
        className,
      )}
      data-slot="page-body"
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// <Section heading children> — semantic section with optional .type-heading
// label. Used inside PageBody for chunking content.

interface SectionProps {
  heading?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Section({ heading, children, className }: SectionProps) {
  return (
    <section
      className={cn("flex flex-col gap-3", className)}
      data-slot="section"
    >
      {heading ? (
        <h2 className="type-heading" data-slot="section-heading">
          {heading}
        </h2>
      ) : null}
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// <Stack> — vertical flex container with a token-aligned gap.

interface StackProps {
  children: ReactNode;
  className?: string;
  /** Tailwind gap value (2 = 0.5rem, 3 = 0.75rem, 4 = 1rem, etc.). */
  gap?: 2 | 3 | 4 | 5 | 6 | 8;
}

const STACK_GAP: Record<NonNullable<StackProps["gap"]>, string> = {
  2: "gap-2",
  3: "gap-3",
  4: "gap-4",
  5: "gap-5",
  6: "gap-6",
  8: "gap-8",
};

export function Stack({ children, className, gap = 4 }: StackProps) {
  return (
    <div
      className={cn("flex flex-col", STACK_GAP[gap], className)}
      data-slot="stack"
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// <Grid cols gap> — responsive grid against F1 spacing tokens. Columns named
// by breakpoint per D-F2-11 (Tailwind defaults). 1-column grid is just a
// flex column; this primitive is for actual multi-column layouts.

interface GridProps {
  children: ReactNode;
  className?: string;
  /** Tailwind gap (default 4 = 1rem). */
  gap?: 2 | 3 | 4 | 5 | 6 | 8;
  /**
   * Column count per breakpoint. `base` is mobile-first (always applied);
   * `sm`/`md`/`lg` override at the corresponding Tailwind breakpoint.
   * Example: `{ base: 1, sm: 2, lg: 3 }` → 1 col on mobile, 2 cols ≥640px,
   * 3 cols ≥1024px.
   */
  cols: {
    base?: 1 | 2 | 3 | 4;
    sm?: 1 | 2 | 3 | 4;
    md?: 1 | 2 | 3 | 4;
    lg?: 1 | 2 | 3 | 4;
  };
}

const COL_CLASSES = {
  base: {
    1: "grid-cols-1",
    2: "grid-cols-2",
    3: "grid-cols-3",
    4: "grid-cols-4",
  },
  sm: {
    1: "sm:grid-cols-1",
    2: "sm:grid-cols-2",
    3: "sm:grid-cols-3",
    4: "sm:grid-cols-4",
  },
  md: {
    1: "md:grid-cols-1",
    2: "md:grid-cols-2",
    3: "md:grid-cols-3",
    4: "md:grid-cols-4",
  },
  lg: {
    1: "lg:grid-cols-1",
    2: "lg:grid-cols-2",
    3: "lg:grid-cols-3",
    4: "lg:grid-cols-4",
  },
} as const;

export function Grid({ children, className, gap = 4, cols }: GridProps) {
  const colClasses = [
    cols.base ? COL_CLASSES.base[cols.base] : "grid-cols-1",
    cols.sm ? COL_CLASSES.sm[cols.sm] : "",
    cols.md ? COL_CLASSES.md[cols.md] : "",
    cols.lg ? COL_CLASSES.lg[cols.lg] : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={cn("grid", colClasses, STACK_GAP[gap], className)}
      data-slot="grid"
    >
      {children}
    </div>
  );
}
