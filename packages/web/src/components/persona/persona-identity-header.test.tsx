/**
 * Spec F2 T13 — PersonaIdentityHeader tests.
 *
 * Verifies the D-F1-5 composite + D-F2-8 constraints-cue gating:
 *   1. Three accents per persona (avatar + name underline + role line).
 *   2. Underline colour matches `derivePersonaIdentityColor(persona).oklch`.
 *   3. Two distinct personas render distinct accents (the §4 individuality
 *      gate for criterion #3).
 *   4. `showConstraints={false}` (default) hides the constraint line.
 *   5. `showConstraints={true}` renders the constraint when present.
 *   6. `showConstraints={true}` + no `constraint` field → no constraint line.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { derivePersonaIdentityColor } from "@/lib/persona-identity";
import { PersonaIdentityHeader } from "./persona-identity-header";

const ASTRID = {
  id: "astrid_tenancy_law",
  name: "Astrid",
  role: "Norwegian tenancy law assistant",
  constraint: "Never gives binding legal advice",
} as const;

const KAI = {
  id: "kai_research",
  name: "Kai",
  role: "Research assistant",
  constraint: "Always cites sources",
} as const;

describe("PersonaIdentityHeader", () => {
  it("renders the D-F1-5 composite — avatar + name + role for one persona", () => {
    const { container, getByText } = render(
      <PersonaIdentityHeader persona={ASTRID} />,
    );
    // Avatar slot (carries the identity-coloured fill + initials).
    expect(
      container.querySelector('[data-slot="persona-identity-header"]'),
    ).not.toBeNull();
    // Name with underline slot.
    const nameEl = container.querySelector(
      '[data-slot="persona-identity-name"]',
    );
    expect(nameEl).not.toBeNull();
    expect(nameEl?.textContent).toBe(ASTRID.name);
    // Role line.
    expect(getByText(ASTRID.role)).not.toBeNull();
  });

  it("paints the 1px underline beneath the name in the persona's identity colour", () => {
    const { container } = render(<PersonaIdentityHeader persona={ASTRID} />);
    const nameEl = container.querySelector(
      '[data-slot="persona-identity-name"]',
    ) as HTMLElement | null;
    expect(nameEl).not.toBeNull();
    const expected = derivePersonaIdentityColor(ASTRID).oklch;
    // The inline style sets borderBottom: `1px solid ${oklch}` — both pieces
    // must be present.
    expect(nameEl?.style.borderBottom).toContain("1px");
    expect(nameEl?.style.borderBottom).toContain(expected);
  });

  it("renders two distinct personas with distinct identity-colour underlines (§4 individuality)", () => {
    const { container: astridC } = render(
      <PersonaIdentityHeader persona={ASTRID} />,
    );
    const { container: kaiC } = render(<PersonaIdentityHeader persona={KAI} />);
    const astridUnderline = (
      astridC.querySelector(
        '[data-slot="persona-identity-name"]',
      ) as HTMLElement
    ).style.borderBottom;
    const kaiUnderline = (
      kaiC.querySelector('[data-slot="persona-identity-name"]') as HTMLElement
    ).style.borderBottom;
    expect(astridUnderline).not.toEqual(kaiUnderline);
    expect(astridUnderline).toContain(derivePersonaIdentityColor(ASTRID).oklch);
    expect(kaiUnderline).toContain(derivePersonaIdentityColor(KAI).oklch);
  });

  it("hides the constraint line by default (showConstraints=false)", () => {
    const { container } = render(<PersonaIdentityHeader persona={ASTRID} />);
    expect(
      container.querySelector('[data-slot="persona-identity-constraint"]'),
    ).toBeNull();
  });

  it("renders the constraint line when showConstraints=true and constraint is set", () => {
    const { container, getByText } = render(
      <PersonaIdentityHeader persona={ASTRID} showConstraints />,
    );
    expect(
      container.querySelector('[data-slot="persona-identity-constraint"]'),
    ).not.toBeNull();
    expect(getByText(ASTRID.constraint)).not.toBeNull();
  });

  it("hides the constraint line when showConstraints=true but no constraint is provided", () => {
    const noConstraint = { id: "x", name: "X", role: "Role" } as const;
    const { container } = render(
      <PersonaIdentityHeader persona={noConstraint} showConstraints />,
    );
    expect(
      container.querySelector('[data-slot="persona-identity-constraint"]'),
    ).toBeNull();
  });
});
