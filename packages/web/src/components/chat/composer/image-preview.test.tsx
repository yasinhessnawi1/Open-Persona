import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/i18n/messages/en.json";
import type { ImageAttachment } from "./attach-state";
import { ComposerImagePreview } from "./image-preview";

function renderWithIntl(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  );
}

const FILE = new File([new Uint8Array(100)], "photo.png", {
  type: "image/png",
});

function attachment(
  state: "pending" | "uploading" | "success" | "error",
  overrides?: Partial<Record<string, unknown>>,
): ImageAttachment {
  const base = { kind: "image" as const, id: "i1", file: FILE };
  switch (state) {
    case "pending":
      return { ...base, state: "pending" };
    case "uploading":
      return { ...base, state: "uploading", progress: 0.4, ...overrides };
    case "success":
      return {
        ...base,
        state: "success",
        workspacePath: "uploads/photo.png",
        mediaType: "image/png",
      };
    case "error":
      return {
        ...base,
        state: "error",
        reason: "server_rejected",
        detail: "magic bytes mismatch",
        ...overrides,
      };
  }
}

describe("<ComposerImagePreview>", () => {
  beforeEach(() => {
    let n = 0;
    globalThis.URL.createObjectURL = vi.fn(() => {
      n += 1;
      return `blob:fake-${n}`;
    });
    globalThis.URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the image thumbnail via useObjectURL", () => {
    renderWithIntl(
      <ComposerImagePreview
        attachment={attachment("success")}
        onRemove={vi.fn()}
      />,
    );
    const img = screen.getByRole("img") as HTMLImageElement;
    expect(img.src).toMatch(/^blob:fake-/);
    expect(img.alt).toBe("photo.png");
  });

  it("remove button fires onRemove with the attachment id", () => {
    const onRemove = vi.fn();
    renderWithIntl(
      <ComposerImagePreview
        attachment={attachment("success")}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByLabelText(en.chat.composer.attach.remove));
    expect(onRemove).toHaveBeenCalledWith("i1");
  });

  it("shows determinate progress for uploading state with known progress", () => {
    const { container } = renderWithIntl(
      <ComposerImagePreview
        attachment={attachment("uploading", { progress: 0.4 })}
        onRemove={vi.fn()}
      />,
    );
    const bar = container.querySelector("div[style*='width']") as HTMLElement;
    expect(bar.style.width).toBe("40%");
  });

  it("shows indeterminate progress (100% bar) when progress is null", () => {
    const { container } = renderWithIntl(
      <ComposerImagePreview
        attachment={attachment("uploading", { progress: null })}
        onRemove={vi.fn()}
      />,
    );
    const bar = container.querySelector("div[style*='width']") as HTMLElement;
    expect(bar.style.width).toBe("100%");
  });

  it("surfaces error detail with role=alert (F2 error voice)", () => {
    renderWithIntl(
      <ComposerImagePreview
        attachment={attachment("error")}
        onRemove={vi.fn()}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("magic bytes mismatch");
  });

  it("ARIA remove label comes from i18n keys (T20 a11y discipline)", () => {
    renderWithIntl(
      <ComposerImagePreview
        attachment={attachment("success")}
        onRemove={vi.fn()}
      />,
    );
    const removeBtn = screen.getByLabelText(en.chat.composer.attach.remove);
    expect(removeBtn).toBeDefined();
  });
});
