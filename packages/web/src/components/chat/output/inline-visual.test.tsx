import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import en from "@/i18n/messages/en.json";

import { InlineVisual } from "./inline-visual";

// Mock the F3 byte-loading hook — F4's InlineVisual COMPOSES <AuthedImage>;
// the hook's behaviour is exercised by F3's own tests. Here we just stub a
// stable src so the loaded <img> branch renders.
vi.mock("@/lib/hooks/use-authed-image-blob-url", () => ({
  useAuthedImageBlobUrl: () => ({
    src: "blob:fake-src",
    loading: false,
    error: null,
  }),
}));

function renderWithIntl(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("<InlineVisual> (T05)", () => {
  describe("renders the F3 AuthedImage byte-loader", () => {
    it("composes AuthedImage with the workspace_path + alt", () => {
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="uploads/abc.png"
          mediaType="image/png"
          intent="image"
          alt="a red bicycle"
        />,
      );
      const img = screen.getByAltText("a red bicycle");
      expect(img).toBeInTheDocument();
      expect(img).toHaveAttribute("src", "blob:fake-src");
    });

    it("wraps the visual in a <figure> with intent dataset", () => {
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="charts/q1.png"
          mediaType="image/png"
          intent="chart"
          alt="Q1 revenue"
        />,
      );
      const figure = screen
        .getByAltText("Q1 revenue")
        .closest('[data-slot="inline-visual"]');
      expect(figure?.tagName).toBe("FIGURE");
      expect(figure).toHaveAttribute("data-intent", "chart");
    });
  });

  describe("intent discriminator drives caption surface", () => {
    it("intent=image renders `caption` beneath the image", () => {
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="uploads/abc.png"
          mediaType="image/png"
          intent="image"
          alt="a cat"
          caption="A cat sitting in a sunbeam"
        />,
      );
      expect(
        screen.getByText("A cat sitting in a sunbeam"),
      ).toBeInTheDocument();
      expect(screen.getByText("A cat sitting in a sunbeam").tagName).toBe(
        "FIGCAPTION",
      );
    });

    it("intent=chart renders `prose_context` beneath the chart", () => {
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="charts/q1.png"
          mediaType="image/png"
          intent="chart"
          alt="Q1 revenue"
          prose_context="Q1 revenue rose 12% year-over-year, driven by enterprise sales."
        />,
      );
      expect(
        screen.getByText(/Q1 revenue rose 12% year-over-year/),
      ).toBeInTheDocument();
    });

    it("intent=image IGNORES prose_context (not the right surface)", () => {
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="uploads/abc.png"
          mediaType="image/png"
          intent="image"
          alt="x"
          prose_context="this should not render for an image"
        />,
      );
      expect(
        screen.queryByText("this should not render for an image"),
      ).not.toBeInTheDocument();
    });

    it("intent=chart IGNORES caption (not the right surface)", () => {
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="charts/q1.png"
          mediaType="image/png"
          intent="chart"
          alt="x"
          caption="this should not render for a chart"
        />,
      );
      expect(
        screen.queryByText("this should not render for a chart"),
      ).not.toBeInTheDocument();
    });

    it("no caption or prose_context → no <figcaption> element", () => {
      const { container } = renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="uploads/abc.png"
          mediaType="image/png"
          intent="image"
          alt="x"
        />,
      );
      expect(container.querySelector("figcaption")).toBeNull();
    });

    it("empty-string caption is treated as absent", () => {
      const { container } = renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="uploads/abc.png"
          mediaType="image/png"
          intent="image"
          alt="x"
          caption=""
        />,
      );
      expect(container.querySelector("figcaption")).toBeNull();
    });
  });

  describe("view-larger affordance", () => {
    it("renders a click-to-zoom button when onViewLarger is provided", () => {
      const onViewLarger = vi.fn();
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="charts/q1.png"
          mediaType="image/png"
          intent="chart"
          alt="Q1 chart"
          onViewLarger={onViewLarger}
        />,
      );
      const btn = screen.getByRole("button");
      expect(btn).toBeInTheDocument();
      // The aria-label comes from the next-intl key (i18n key wired).
      expect(btn).toHaveAttribute(
        "aria-label",
        expect.stringContaining("Q1 chart"),
      );
    });

    it("clicking the button invokes onViewLarger", () => {
      const onViewLarger = vi.fn();
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="charts/q1.png"
          mediaType="image/png"
          intent="chart"
          alt="Q1 chart"
          onViewLarger={onViewLarger}
        />,
      );
      fireEvent.click(screen.getByRole("button"));
      expect(onViewLarger).toHaveBeenCalledTimes(1);
    });

    it("renders no button when onViewLarger is omitted (non-interactive)", () => {
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="charts/q1.png"
          mediaType="image/png"
          intent="chart"
          alt="Q1 chart"
        />,
      );
      expect(screen.queryByRole("button")).toBeNull();
    });
  });

  describe("default sizing (D-F4-2)", () => {
    it("figure container has max-w-[480px] cap", () => {
      const { container } = renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="uploads/abc.png"
          mediaType="image/png"
          intent="image"
          alt="x"
        />,
      );
      const figure = container.querySelector("figure");
      expect(figure).toBeInTheDocument();
      // Default sizing cap per D-F4-2 (~480px inline, responsive on mobile).
      expect(figure?.className).toContain("max-w-[480px]");
    });
  });

  describe("intent edge — Spec 15 generated image at uploads/", () => {
    it("renders intent=image for a Spec 15 path verbatim", () => {
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="uploads/blake2bdigest.png"
          mediaType="image/png"
          intent="image"
          alt="generated"
        />,
      );
      const figure = screen
        .getByAltText("generated")
        .closest('[data-slot="inline-visual"]');
      expect(figure).toHaveAttribute("data-intent", "image");
    });
  });

  describe("intent edge — Spec 17 chart at charts/", () => {
    it("renders intent=chart for a Spec 17 path verbatim", () => {
      renderWithIntl(
        <InlineVisual
          personaId="p1"
          workspacePath="charts/abc.png"
          mediaType="image/png"
          intent="chart"
          alt="chart"
        />,
      );
      const figure = screen
        .getByAltText("chart")
        .closest('[data-slot="inline-visual"]');
      expect(figure).toHaveAttribute("data-intent", "chart");
    });
  });
});
