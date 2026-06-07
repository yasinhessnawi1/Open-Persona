import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import en from "@/i18n/messages/en.json";

import { WorkingState } from "./working-state";

function renderWithIntl(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("<WorkingState> (T08)", () => {
  describe("operation-default labels (contextual, never bare 'Loading…')", () => {
    it("operation=image_gen renders 'Generating image…'", () => {
      renderWithIntl(<WorkingState operation="image_gen" />);
      expect(screen.getByText("Generating image…")).toBeInTheDocument();
    });

    it("operation=code_exec renders 'Running code…'", () => {
      renderWithIntl(<WorkingState operation="code_exec" />);
      expect(screen.getByText("Running code…")).toBeInTheDocument();
    });

    it("operation=doc_gen renders 'Producing document…'", () => {
      renderWithIntl(<WorkingState operation="doc_gen" />);
      expect(screen.getByText("Producing document…")).toBeInTheDocument();
    });
  });

  describe("label override", () => {
    it("custom label takes precedence over the operation default", () => {
      renderWithIntl(
        <WorkingState operation="code_exec" label="Running my-custom-tool…" />,
      );
      expect(screen.getByText("Running my-custom-tool…")).toBeInTheDocument();
      // The default label MUST NOT appear when the override is set.
      expect(screen.queryByText("Running code…")).not.toBeInTheDocument();
    });
  });

  describe("a11y — implicit role=status on <output>", () => {
    it("renders inside an <output> element with implicit role=status", () => {
      const { container } = renderWithIntl(
        <WorkingState operation="image_gen" />,
      );
      const root = container.querySelector('[data-slot="working-state"]');
      expect(root?.tagName).toBe("OUTPUT");
      // <output> has implicit role=status (polite live region); the
      // accessibility tree resolves it without an explicit attribute.
      expect(screen.getByRole("status")).toBe(root);
    });

    it("aria-label mirrors the visible label", () => {
      renderWithIntl(<WorkingState operation="image_gen" />);
      // Two text nodes share the same content (aria-label on the <output>
      // and the visible <span>); RTL findByLabelText resolves on aria-label.
      const root = screen.getByLabelText("Generating image…");
      expect(root).toBeInTheDocument();
    });

    it("the three pulse dots are aria-hidden (label carries semantics)", () => {
      const { container } = renderWithIntl(
        <WorkingState operation="code_exec" />,
      );
      const dotsContainer = container.querySelector('[aria-hidden="true"]');
      expect(dotsContainer).toBeInTheDocument();
      // Three dot children (the F1 motion pattern from message-element.tsx:504-528).
      expect(dotsContainer?.children).toHaveLength(3);
    });
  });

  describe("F1 motion + reduced-motion compliance", () => {
    it("each dot has animate-pulse with a staggered delay", () => {
      const { container } = renderWithIntl(
        <WorkingState operation="code_exec" />,
      );
      const dots = container.querySelectorAll('[aria-hidden="true"] > span');
      expect(dots).toHaveLength(3);
      for (const dot of dots) {
        expect(dot.className).toContain("animate-pulse");
        // Reduced-motion silences the pulse without removing the dots.
        expect(dot.className).toContain("motion-reduce:animate-none");
      }
      // Stagger via inline style — 0ms, 200ms, 400ms (matches F1 cadence).
      const delays = Array.from(dots).map(
        (d) => (d as HTMLElement).style.animationDelay,
      );
      expect(delays).toEqual(["0ms", "200ms", "400ms"]);
    });
  });

  describe("data attributes for cross-surface testing", () => {
    it("data-operation exposes the discriminator", () => {
      const { container } = renderWithIntl(
        <WorkingState operation="doc_gen" />,
      );
      expect(
        container.querySelector('[data-slot="working-state"]'),
      ).toHaveAttribute("data-operation", "doc_gen");
    });
  });

  describe("F2 voice (italic + muted-foreground)", () => {
    it("the root carries .type-ui italic text-muted-foreground", () => {
      const { container } = renderWithIntl(
        <WorkingState operation="image_gen" />,
      );
      const root = container.querySelector('[data-slot="working-state"]');
      expect(root?.className).toContain("type-ui");
      expect(root?.className).toContain("italic");
      expect(root?.className).toContain("text-muted-foreground");
    });
  });
});
