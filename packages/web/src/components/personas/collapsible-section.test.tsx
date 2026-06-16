/**
 * Collapsible section cards + the left timeline nav (persona-editor redesign).
 *
 * Verifies: default-open vs collapsed; the chevron trigger toggles + carries
 * aria-expanded; collapsed content is marked inert; the nav lists registered
 * sections in order and expands a collapsed section on click.
 */
import { fireEvent, render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import messages from "@/i18n/messages/en.json";
import {
  CollapsibleSection,
  SectionGroup,
  SectionTimelineNav,
} from "./collapsible-section";

function renderWith(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("CollapsibleSection (standalone, local state)", () => {
  it("respects defaultOpen and toggles via the trigger", () => {
    const { container, getByText } = renderWith(
      <CollapsibleSection id="identity" title="Identity" defaultOpen>
        <p>identity body</p>
      </CollapsibleSection>,
    );
    const card = container.querySelector("#identity");
    const trigger = card?.querySelector('[data-slot="collapsible-trigger"]');
    expect(card?.getAttribute("data-open")).toBe("true");
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");
    expect(getByText("identity body")).toBeInTheDocument();

    fireEvent.click(trigger as HTMLElement);
    expect(card?.getAttribute("data-open")).toBe("false");
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
  });

  it("starts collapsed by default and marks the body inert", () => {
    const { container } = renderWith(
      <CollapsibleSection id="extra" title="Extra">
        <p>hidden body</p>
      </CollapsibleSection>,
    );
    const card = container.querySelector("#extra");
    expect(card?.getAttribute("data-open")).toBe("false");
    // The content stays in the DOM (so form state persists) but is inert.
    expect(card?.querySelector("[inert]")).not.toBeNull();
  });
});

describe("SectionTimelineNav (grouped)", () => {
  it("lists registered sections in order and expands one on click", () => {
    const { container } = renderWith(
      <SectionGroup>
        <SectionTimelineNav />
        <CollapsibleSection id="identity" title="Identity" defaultOpen>
          <p>a</p>
        </CollapsibleSection>
        <CollapsibleSection id="worldview" title="Worldview">
          <p>b</p>
        </CollapsibleSection>
      </SectionGroup>,
    );
    const nav = container.querySelector('[data-slot="section-timeline-nav"]');
    expect(nav).not.toBeNull();
    const links = Array.from(nav?.querySelectorAll("a") ?? []);
    expect(links.map((a) => a.textContent)).toEqual(["Identity", "Worldview"]);

    // Worldview starts collapsed; clicking its nav link opens it.
    const worldviewCard = container.querySelector("#worldview");
    expect(worldviewCard?.getAttribute("data-open")).toBe("false");
    const worldviewLink = links.find((a) => a.textContent === "Worldview");
    fireEvent.click(worldviewLink as HTMLElement);
    expect(worldviewCard?.getAttribute("data-open")).toBe("true");
  });
});
