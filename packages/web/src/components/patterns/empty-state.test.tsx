/**
 * Spec F2 T22 — EmptyState tests.
 *
 * Verifies the inviting-voice pattern renders correctly with optional icon,
 * description, and action slots.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState } from "./empty-state";

describe("EmptyState", () => {
  it("renders title in .type-heading", () => {
    const { container } = render(<EmptyState title="No personas yet" />);
    const title = container.querySelector('[data-slot="empty-state-title"]');
    expect(title?.textContent).toBe("No personas yet");
    expect(title?.className).toContain("type-heading");
  });

  it("renders description in muted-foreground when provided", () => {
    const { container } = render(
      <EmptyState
        title="No personas yet"
        description="Create your first persona to start a conversation."
      />,
    );
    const desc = container.querySelector(
      '[data-slot="empty-state-description"]',
    );
    expect(desc?.textContent).toContain("Create your first persona");
    expect(desc?.className).toContain("text-muted-foreground");
  });

  it("renders icon slot in muted-foreground when provided", () => {
    const { container } = render(
      <EmptyState
        title="No personas yet"
        icon={<svg data-testid="ic" aria-hidden="true" />}
      />,
    );
    const iconSlot = container.querySelector('[data-slot="empty-state-icon"]');
    expect(iconSlot).not.toBeNull();
    expect(iconSlot?.getAttribute("aria-hidden")).toBe("true");
  });

  it("renders action slot when provided", () => {
    const { container } = render(
      <EmptyState
        title="No personas yet"
        action={
          <a href="/personas/new" data-testid="cta">
            Create
          </a>
        }
      />,
    );
    expect(
      container.querySelector('[data-slot="empty-state-action"]'),
    ).not.toBeNull();
    expect(container.querySelector("[data-testid=cta]")?.textContent).toBe(
      "Create",
    );
  });

  it("uses the dashed border (inviting, not loud)", () => {
    const { container } = render(<EmptyState title="x" />);
    const wrap = container.querySelector('[data-slot="empty-state"]');
    expect(wrap?.className).toContain("border-dashed");
  });
});
