/**
 * Spec F2 T19 — persona-context tests.
 *
 * Verifies usePersona() reads the active persona from <PersonaProvider> and
 * returns null when no provider is present.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PersonaProvider, usePersona } from "./persona-context";

function Probe() {
  const persona = usePersona();
  return (
    <div data-testid="probe">
      {persona ? `${persona.name}|${persona.id}|${persona.role ?? ""}` : "null"}
    </div>
  );
}

describe("PersonaContext", () => {
  it("returns null outside a PersonaProvider", () => {
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("probe").textContent).toBe("null");
  });

  it("returns the persona inside a PersonaProvider", () => {
    const { getByTestId } = render(
      <PersonaProvider
        persona={{ id: "astrid", name: "Astrid", role: "Tenancy assistant" }}
      >
        <Probe />
      </PersonaProvider>,
    );
    expect(getByTestId("probe").textContent).toBe(
      "Astrid|astrid|Tenancy assistant",
    );
  });

  it("passes null through when route explicitly clears persona", () => {
    const { getByTestId } = render(
      <PersonaProvider persona={null}>
        <Probe />
      </PersonaProvider>,
    );
    expect(getByTestId("probe").textContent).toBe("null");
  });
});
