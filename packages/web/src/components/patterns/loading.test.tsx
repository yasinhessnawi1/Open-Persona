/**
 * Spec F2 T21 — Loading patterns tests.
 *
 * Verifies skeleton family + spinner render correctly with the F1 token-clean
 * classes and pulse animation. Decorative semantics confirmed (aria-hidden on
 * skeletons; aria-label exposed on Spinner).
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  SkeletonAvatar,
  SkeletonBlock,
  SkeletonLine,
  Spinner,
} from "./loading";

describe("SkeletonLine", () => {
  it("renders a pulse bar marked aria-hidden", () => {
    const { container } = render(<SkeletonLine />);
    const el = container.querySelector('[data-slot="skeleton-line"]');
    expect(el).not.toBeNull();
    expect(el?.getAttribute("aria-hidden")).toBe("true");
    expect(el?.className).toContain("animate-pulse");
    expect(el?.className).toContain("bg-muted");
  });
});

describe("SkeletonBlock", () => {
  it("renders 3 lines by default with the last one shorter", () => {
    const { container } = render(<SkeletonBlock />);
    const bars = container.querySelectorAll(
      '[data-slot="skeleton-block"] > div',
    );
    expect(bars.length).toBe(3);
    expect(bars[0].className).toContain("w-full");
    expect(bars[2].className).toContain("w-2/3");
  });

  it("renders the requested number of lines", () => {
    const { container } = render(<SkeletonBlock lines={5} />);
    const bars = container.querySelectorAll(
      '[data-slot="skeleton-block"] > div',
    );
    expect(bars.length).toBe(5);
  });
});

describe("SkeletonAvatar", () => {
  it("applies sm/md/lg sizing classes", () => {
    const { container: smC } = render(<SkeletonAvatar size="sm" />);
    expect(
      smC.querySelector('[data-slot="skeleton-avatar"]')?.className,
    ).toContain("size-6");

    const { container: mdC } = render(<SkeletonAvatar size="md" />);
    expect(
      mdC.querySelector('[data-slot="skeleton-avatar"]')?.className,
    ).toContain("size-10");

    const { container: lgC } = render(<SkeletonAvatar size="lg" />);
    expect(
      lgC.querySelector('[data-slot="skeleton-avatar"]')?.className,
    ).toContain("size-16");
  });

  it("is circular and pulsing", () => {
    const { container } = render(<SkeletonAvatar />);
    const el = container.querySelector('[data-slot="skeleton-avatar"]');
    expect(el?.className).toContain("rounded-full");
    expect(el?.className).toContain("animate-pulse");
  });
});

describe("Spinner", () => {
  it("exposes an accessible name", () => {
    const { container } = render(<Spinner label="Saving" />);
    const el = container.querySelector('[data-slot="spinner"]');
    expect(el?.getAttribute("aria-label")).toBe("Saving");
  });

  it("defaults the aria-label to 'Loading'", () => {
    const { container } = render(<Spinner />);
    const el = container.querySelector('[data-slot="spinner"]');
    expect(el?.getAttribute("aria-label")).toBe("Loading");
  });

  it("uses --motion-duration-slow for one rotation (token consumption)", () => {
    const { container } = render(<Spinner />);
    const svg = container.querySelector('[data-slot="spinner"] svg');
    // getAttribute("class") works for both HTMLElement and SVGElement;
    // SVG's `.className` is SVGAnimatedString in some DOM typings.
    const cls = svg?.getAttribute("class") ?? "";
    expect(cls).toContain("duration-[var(--motion-duration-slow)]");
    expect(cls).toContain("animate-spin");
  });
});
