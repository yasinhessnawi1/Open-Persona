import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import en from "@/i18n/messages/en.json";

import { ImageLightbox } from "./image-lightbox";

// Byte loader + Clerk are stubbed so the lightbox renders deterministically.
vi.mock("@/lib/hooks/use-authed-image-blob-url", () => ({
  useAuthedImageBlobUrl: () => ({
    src: "blob:fake",
    loading: false,
    error: null,
  }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: vi.fn().mockResolvedValue("fake-jwt") }),
}));

function renderWithIntl(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("<ImageLightbox> (T12)", () => {
  let onClose: ReturnType<typeof vi.fn> & (() => void);
  const originalBodyOverflow = document.body.style.overflow;

  beforeEach(() => {
    onClose = vi.fn() as ReturnType<typeof vi.fn> & (() => void);
  });

  afterEach(() => {
    document.body.style.overflow = originalBodyOverflow;
  });

  describe("rendering", () => {
    it("renders nothing when open=false (no portal, no DOM cost)", () => {
      const { container } = renderWithIntl(
        <ImageLightbox
          open={false}
          personaId="p1"
          workspacePath="uploads/abc.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      expect(
        container.querySelector('[data-slot="image-lightbox"]'),
      ).toBeNull();
      expect(
        document.body.querySelector('[data-slot="image-lightbox"]'),
      ).toBeNull();
    });

    it("portals into document.body when open=true", () => {
      renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="charts/q1.png"
          mediaType="image/png"
          alt="Q1 chart"
          onClose={onClose}
        />,
      );
      const dialog = document.body.querySelector(
        '[data-slot="image-lightbox"]',
      );
      expect(dialog).toBeInTheDocument();
      expect(dialog).toHaveAttribute("role", "dialog");
      expect(dialog).toHaveAttribute("aria-modal", "true");
    });

    it("renders the image via AuthedImage with the alt + workspace_path", () => {
      renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="charts/q1.png"
          mediaType="image/png"
          alt="Q1 chart"
          onClose={onClose}
        />,
      );
      const img = screen.getByAltText("Q1 chart");
      expect(img).toBeInTheDocument();
      expect(img).toHaveAttribute("src", "blob:fake");
    });

    it("renders close + download buttons in the toolbar", () => {
      renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="uploads/x.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      expect(
        screen.getByRole("button", { name: "Close lightbox" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Download image" }),
      ).toBeInTheDocument();
    });
  });

  describe("dismiss affordances", () => {
    it("clicking the backdrop fires onClose", () => {
      renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="uploads/x.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      const dialog = document.body.querySelector(
        '[data-slot="image-lightbox"]',
      ) as HTMLElement;
      fireEvent.click(dialog);
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("clicking inside the panel does NOT close (event.target !== currentTarget)", () => {
      renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="uploads/x.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      const panel = document.body.querySelector(
        '[data-slot="image-lightbox-panel"]',
      ) as HTMLElement;
      fireEvent.click(panel);
      expect(onClose).not.toHaveBeenCalled();
    });

    it("clicking the close button fires onClose", () => {
      renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="uploads/x.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Close lightbox" }));
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("pressing ESC fires onClose", () => {
      renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="uploads/x.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      fireEvent.keyDown(document, { key: "Escape" });
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("pressing a non-ESC key does NOT close (e.g. Enter / Tab)", () => {
      renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="uploads/x.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      fireEvent.keyDown(document, { key: "Enter" });
      fireEvent.keyDown(document, { key: "Tab" });
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  describe("body scroll lock", () => {
    it("locks document.body overflow while open", () => {
      const { rerender } = renderWithIntl(
        <ImageLightbox
          open={false}
          personaId="p1"
          workspacePath="uploads/x.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      expect(document.body.style.overflow).not.toBe("hidden");
      rerender(
        <NextIntlClientProvider locale="en" messages={en}>
          <ImageLightbox
            open={true}
            personaId="p1"
            workspacePath="uploads/x.png"
            mediaType="image/png"
            alt="x"
            onClose={onClose}
          />
        </NextIntlClientProvider>,
      );
      expect(document.body.style.overflow).toBe("hidden");
    });

    it("restores body overflow when open flips back to false", () => {
      const { rerender } = renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="uploads/x.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      expect(document.body.style.overflow).toBe("hidden");
      rerender(
        <NextIntlClientProvider locale="en" messages={en}>
          <ImageLightbox
            open={false}
            personaId="p1"
            workspacePath="uploads/x.png"
            mediaType="image/png"
            alt="x"
            onClose={onClose}
          />
        </NextIntlClientProvider>,
      );
      expect(document.body.style.overflow).not.toBe("hidden");
    });
  });

  describe("event listener cleanup", () => {
    it("removes the document keydown listener when closed (no leak)", () => {
      const { rerender } = renderWithIntl(
        <ImageLightbox
          open={true}
          personaId="p1"
          workspacePath="uploads/x.png"
          mediaType="image/png"
          alt="x"
          onClose={onClose}
        />,
      );
      rerender(
        <NextIntlClientProvider locale="en" messages={en}>
          <ImageLightbox
            open={false}
            personaId="p1"
            workspacePath="uploads/x.png"
            mediaType="image/png"
            alt="x"
            onClose={onClose}
          />
        </NextIntlClientProvider>,
      );
      // After unmount-of-effect, ESC keydown no longer fires onClose.
      fireEvent.keyDown(document, { key: "Escape" });
      expect(onClose).not.toHaveBeenCalled();
    });
  });
});
