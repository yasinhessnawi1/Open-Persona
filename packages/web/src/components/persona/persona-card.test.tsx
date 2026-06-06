/**
 * Spec F2 T14 — PersonaCard tests.
 *
 * Verifies:
 *   1. Name + role render.
 *   2. Avatar slot is present and identity-coloured (delegates to T06 avatar).
 *   3. Two distinct personas render distinct avatar colours (the §4
 *      individuality gate; the scaffold violation this replaces).
 *   4. `href` makes the card a navigating <Link>; absent → static.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { derivePersonaIdentityColor } from "@/lib/persona-identity";
import { PersonaCard } from "./persona-card";

const ASTRID = {
  id: "astrid_tenancy_law",
  name: "Astrid",
  role: "Norwegian tenancy law assistant",
} as const;

const MAREN = {
  id: "maren_writing_coach",
  name: "Maren",
  role: "Writing coach",
} as const;

describe("PersonaCard", () => {
  it("renders the persona's name and role", () => {
    const { getByText } = render(<PersonaCard persona={ASTRID} />);
    expect(getByText("Astrid")).not.toBeNull();
    expect(getByText("Norwegian tenancy law assistant")).not.toBeNull();
  });

  it("renders the PersonaAvatar in the persona's identity colour (closes the D-F1-5 violation)", () => {
    const { container } = render(<PersonaCard persona={ASTRID} />);
    const avatar = container.querySelector(
      '[role="img"][aria-label="Astrid"]',
    ) as HTMLElement | null;
    expect(avatar).not.toBeNull();
    // The PersonaAvatar inline-styles `background` to the derived OKLCH.
    const expected = derivePersonaIdentityColor(ASTRID).oklch;
    expect(avatar?.style.background).toContain(expected);
  });

  it("renders two distinct personas with distinct avatar colours (§4 individuality)", () => {
    const { container: astridC } = render(<PersonaCard persona={ASTRID} />);
    const { container: marenC } = render(<PersonaCard persona={MAREN} />);
    const astridBg = (astridC.querySelector('[role="img"]') as HTMLElement)
      .style.background;
    const marenBg = (marenC.querySelector('[role="img"]') as HTMLElement).style
      .background;
    expect(astridBg).not.toEqual(marenBg);
    expect(astridBg).toContain(derivePersonaIdentityColor(ASTRID).oklch);
    expect(marenBg).toContain(derivePersonaIdentityColor(MAREN).oklch);
  });

  it("wraps in a <Link> when href is provided", () => {
    const { container } = render(
      <PersonaCard persona={ASTRID} href="/personas/astrid_tenancy_law" />,
    );
    const link = container.querySelector("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/personas/astrid_tenancy_law");
  });

  it("renders without a <Link> when href is absent", () => {
    const { container } = render(<PersonaCard persona={ASTRID} />);
    expect(container.querySelector("a")).toBeNull();
  });
});
