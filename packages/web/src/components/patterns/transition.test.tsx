/**
 * Spec F2 T23 — Transition primitives tests.
 *
 * Verifies the F1 motion-duration tokens land on the rendered element +
 * the SlideTransition's `from` variants apply the correct direction class.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FadeTransition, SlideTransition } from "./transition";

describe("FadeTransition", () => {
  it("defaults to --motion-duration-normal", () => {
    const { container } = render(
      <FadeTransition>
        <p>x</p>
      </FadeTransition>,
    );
    const wrap = container.querySelector('[data-slot="fade-transition"]');
    expect(wrap?.className).toContain(
      "duration-[var(--motion-duration-normal)]",
    );
    expect(wrap?.className).toContain("animate-in");
    expect(wrap?.className).toContain("fade-in");
  });

  it("applies --motion-duration-fast when speed='fast'", () => {
    const { container } = render(
      <FadeTransition speed="fast">
        <p>x</p>
      </FadeTransition>,
    );
    expect(
      container.querySelector('[data-slot="fade-transition"]')?.className,
    ).toContain("duration-[var(--motion-duration-fast)]");
  });

  it("applies --motion-duration-slow when speed='slow'", () => {
    const { container } = render(
      <FadeTransition speed="slow">
        <p>x</p>
      </FadeTransition>,
    );
    expect(
      container.querySelector('[data-slot="fade-transition"]')?.className,
    ).toContain("duration-[var(--motion-duration-slow)]");
  });
});

describe("SlideTransition", () => {
  it("defaults to slide from top with normal duration + emphasized ease", () => {
    const { container } = render(
      <SlideTransition>
        <p>x</p>
      </SlideTransition>,
    );
    const wrap = container.querySelector('[data-slot="slide-transition"]');
    expect(wrap?.getAttribute("data-from")).toBe("top");
    expect(wrap?.className).toContain("slide-in-from-top-2");
    expect(wrap?.className).toContain(
      "duration-[var(--motion-duration-normal)]",
    );
    expect(wrap?.className).toContain("ease-[var(--motion-ease-emphasized)]");
  });

  it("applies the correct slide-in class per direction", () => {
    const dirs = [
      { from: "top" as const, cls: "slide-in-from-top-2" },
      { from: "bottom" as const, cls: "slide-in-from-bottom-2" },
      { from: "left" as const, cls: "slide-in-from-left-2" },
      { from: "right" as const, cls: "slide-in-from-right-2" },
    ];
    for (const { from, cls } of dirs) {
      const { container } = render(
        <SlideTransition from={from}>
          <p>x</p>
        </SlideTransition>,
      );
      const wrap = container.querySelector('[data-slot="slide-transition"]');
      expect(wrap?.getAttribute("data-from")).toBe(from);
      expect(wrap?.className).toContain(cls);
    }
  });
});
