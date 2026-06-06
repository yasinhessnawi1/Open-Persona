/**
 * Spec F2 T22 — ErrorState tests.
 *
 * Verifies the D-F2-9 one-template-with-overrides pattern handles the four
 * supported statuses (default / 422 / 429 / 402) + the pydantic-detail
 * helper renders Spec-08 422 field-level detail.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ErrorState, pydantic422Detail } from "./error-state";

describe("ErrorState", () => {
  it("renders title + description for the default status", () => {
    const { container } = render(
      <ErrorState
        status="default"
        copy={{
          title: "Something went wrong",
          description: "Try again in a moment.",
        }}
      />,
    );
    expect(
      container.querySelector('[data-slot="error-state-title"]')?.textContent,
    ).toBe("Something went wrong");
    expect(
      container.querySelector('[data-slot="error-state-description"]')
        ?.textContent,
    ).toContain("Try again");
  });

  it("exposes data-status for each variant", () => {
    for (const status of ["default", 422, 429, 402] as const) {
      const { container } = render(
        <ErrorState status={status} copy={{ title: "x" }} />,
      );
      const wrap = container.querySelector('[data-slot="error-state"]');
      expect(wrap?.getAttribute("data-status")).toBe(String(status));
    }
  });

  it("renders detail slot when provided (e.g., 422 field errors)", () => {
    const { container } = render(
      <ErrorState
        status={422}
        copy={{
          title: "Validation failed",
          detail: pydantic422Detail([
            { loc: ["body", "name"], msg: "Field required" },
            { loc: ["body", "constraints"], msg: "Must be a list" },
          ]),
        }}
      />,
    );
    const detail = container.querySelector('[data-slot="error-state-detail"]');
    expect(detail).not.toBeNull();
    expect(detail?.textContent).toContain("body.name: Field required");
    expect(detail?.textContent).toContain("body.constraints: Must be a list");
  });

  it("renders action slot when provided (e.g., 402 contact-support link)", () => {
    const { container } = render(
      <ErrorState
        status={402}
        copy={{
          title: "Out of credits",
          action: (
            <a href="mailto:support@example.com" data-testid="contact">
              Contact support
            </a>
          ),
        }}
      />,
    );
    expect(
      container.querySelector('[data-slot="error-state-action"]'),
    ).not.toBeNull();
    expect(container.querySelector("[data-testid=contact]")?.textContent).toBe(
      "Contact support",
    );
  });

  it("applies per-status ring tone", () => {
    const tones = [
      { status: "default" as const, ring: "ring-destructive/30" },
      { status: 422 as const, ring: "ring-destructive/30" },
      { status: 429 as const, ring: "ring-tier-mid/40" },
      { status: 402 as const, ring: "ring-primary/40" },
    ];
    for (const { status, ring } of tones) {
      const { container } = render(
        <ErrorState status={status} copy={{ title: "x" }} />,
      );
      const wrap = container.querySelector('[data-slot="error-state"]');
      expect(wrap?.className).toContain(ring);
    }
  });
});

describe("pydantic422Detail helper", () => {
  it("formats loc as dotted path", () => {
    const node = pydantic422Detail([
      { loc: ["body", "identity", "name"], msg: "required" },
    ]);
    const { container } = render(<div>{node}</div>);
    expect(container.textContent).toContain("body.identity.name: required");
  });

  it("omits the loc prefix when loc is empty", () => {
    const node = pydantic422Detail([{ loc: [], msg: "generic" }]);
    const { container } = render(<div>{node}</div>);
    expect(container.textContent).toBe("generic");
  });

  it("omits the loc prefix when loc is absent", () => {
    const node = pydantic422Detail([{ msg: "no-loc" }]);
    const { container } = render(<div>{node}</div>);
    expect(container.textContent).toBe("no-loc");
  });
});
