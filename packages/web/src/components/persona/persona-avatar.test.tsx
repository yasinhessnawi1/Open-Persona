/**
 * Spec F1 T06 — <PersonaAvatar> tests.
 *
 * Asserts D-F1-2: initials-mark in identity-coloured fill; avatar_url override
 * wins; three sizes; identity CSS-vars exported for descendants.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { derivePersonaIdentityColor } from "@/lib/persona-identity";
import { PersonaAvatar } from "./persona-avatar";

describe("PersonaAvatar — default treatment (D-F1-2)", () => {
  it("renders initials-mark when no avatar_url", () => {
    render(
      <PersonaAvatar persona={{ id: "astrid_tenancy_law", name: "Astrid" }} />,
    );
    expect(screen.getByLabelText("Astrid")).toBeInTheDocument();
    expect(screen.getByLabelText("Astrid").textContent).toBe("AS");
  });

  it("derives initials from first + last name for multi-word names", () => {
    render(
      <PersonaAvatar
        persona={{ id: "maren_writing_coach", name: "Maren Writing Coach" }}
      />,
    );
    expect(screen.getByLabelText("Maren Writing Coach").textContent).toBe("MC");
  });

  it("falls back to ? for an empty name", () => {
    render(<PersonaAvatar persona={{ id: "x", name: "" }} />);
    expect(screen.getByLabelText("").textContent).toBe("?");
  });

  it("uppercases all initials", () => {
    render(<PersonaAvatar persona={{ id: "x", name: "lower case" }} />);
    expect(screen.getByLabelText("lower case").textContent).toBe("LC");
  });

  it("applies the derived identity colour as the background", () => {
    const persona = { id: "astrid_tenancy_law", name: "Astrid" };
    const colour = derivePersonaIdentityColor(persona);
    render(<PersonaAvatar persona={persona} />);
    const el = screen.getByLabelText("Astrid");
    expect(el.style.background).toContain(colour.oklch);
  });

  it("exports --identity-h, --identity-l, --identity-c as inline CSS vars for descendants", () => {
    const persona = { id: "astrid_tenancy_law", name: "Astrid" };
    const colour = derivePersonaIdentityColor(persona);
    render(<PersonaAvatar persona={persona} />);
    const el = screen.getByLabelText("Astrid");
    expect(el.style.getPropertyValue("--identity-h")).toBe(String(colour.h));
    expect(el.style.getPropertyValue("--identity-l")).toBe(String(colour.l));
    expect(el.style.getPropertyValue("--identity-c")).toBe(String(colour.c));
  });
});

describe("PersonaAvatar — avatar_url override", () => {
  it("renders an image when avatar_url is set", () => {
    render(
      <PersonaAvatar
        persona={{
          id: "astrid_tenancy_law",
          name: "Astrid",
          avatar_url: "/astrid.png",
        }}
      />,
    );
    const img = document.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe("/astrid.png");
  });

  it("does NOT render initials when avatar_url is set", () => {
    render(
      <PersonaAvatar
        persona={{
          id: "astrid_tenancy_law",
          name: "Astrid",
          avatar_url: "/astrid.png",
        }}
      />,
    );
    expect(screen.queryByLabelText("Astrid")).not.toBeInTheDocument();
  });

  it("still exports --identity-* CSS vars so surrounding accents (header underline / message border) reach the same identity colour", () => {
    const persona = {
      id: "astrid_tenancy_law",
      name: "Astrid",
      avatar_url: "/astrid.png",
    };
    const colour = derivePersonaIdentityColor(persona);
    const { container } = render(<PersonaAvatar persona={persona} />);
    const wrapper = container.querySelector("span");
    expect(wrapper?.style.getPropertyValue("--identity-h")).toBe(
      String(colour.h),
    );
    expect(wrapper?.style.getPropertyValue("--identity-l")).toBe(
      String(colour.l),
    );
    expect(wrapper?.style.getPropertyValue("--identity-c")).toBe(
      String(colour.c),
    );
  });

  it("treats empty string avatar_url as 'no override' (falls through to initials)", () => {
    render(
      <PersonaAvatar
        persona={{ id: "x", name: "Test User", avatar_url: "" }}
      />,
    );
    expect(screen.getByLabelText("Test User").textContent).toBe("TU");
  });

  it("treats null avatar_url as 'no override' (falls through to initials)", () => {
    render(
      <PersonaAvatar
        persona={{ id: "x", name: "Test User", avatar_url: null }}
      />,
    );
    expect(screen.getByLabelText("Test User").textContent).toBe("TU");
  });
});

describe("PersonaAvatar — size variants", () => {
  it("supports sm / md / lg", () => {
    for (const size of ["sm", "md", "lg"] as const) {
      const { container } = render(
        <PersonaAvatar persona={{ id: "x", name: "X" }} size={size} />,
      );
      const el = container.querySelector("span");
      expect(el).not.toBeNull();
    }
  });

  it("defaults to md", () => {
    const { container } = render(
      <PersonaAvatar persona={{ id: "x", name: "X" }} />,
    );
    expect(container.querySelector("span")?.className).toContain("size-10");
  });

  it("merges a caller className without dropping size", () => {
    const { container } = render(
      <PersonaAvatar
        persona={{ id: "x", name: "X" }}
        className="ring-2 ring-primary"
      />,
    );
    const cls = container.querySelector("span")?.className ?? "";
    expect(cls).toContain("ring-2");
    expect(cls).toContain("size-10");
  });
});
