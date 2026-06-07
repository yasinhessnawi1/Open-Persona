import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import en from "@/i18n/messages/en.json";
import { ComposerAttachControl } from "./attach-control";

function renderWithIntl(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  );
}

function file(name: string, type: string, size = 100): File {
  return new File([new Uint8Array(size)], name, { type });
}

function fireFileSelect(input: HTMLInputElement, files: File[]) {
  // jsdom's DataTransfer.items.add isn't reliable; we set files directly.
  Object.defineProperty(input, "files", {
    configurable: true,
    value: files as unknown as FileList,
  });
  fireEvent.change(input);
}

describe("<ComposerAttachControl>", () => {
  it("routes an image to onImageFile", () => {
    const onImage = vi.fn();
    const onDoc = vi.fn();
    const onReject = vi.fn();
    renderWithIntl(
      <ComposerAttachControl
        onImageFile={onImage}
        onDocumentFile={onDoc}
        onReject={onReject}
        currentImageCount={0}
      />,
    );
    const input = screen.getByLabelText(/attach file/i) as HTMLInputElement;
    fireFileSelect(input, [file("a.png", "image/png")]);

    expect(onImage).toHaveBeenCalledTimes(1);
    expect(onImage.mock.calls[0][0].name).toBe("a.png");
    expect(onDoc).not.toHaveBeenCalled();
    expect(onReject).not.toHaveBeenCalled();
  });

  it("routes a PDF to onDocumentFile", () => {
    const onImage = vi.fn();
    const onDoc = vi.fn();
    const onReject = vi.fn();
    renderWithIntl(
      <ComposerAttachControl
        onImageFile={onImage}
        onDocumentFile={onDoc}
        onReject={onReject}
        currentImageCount={0}
      />,
    );
    const input = screen.getByLabelText(/attach file/i) as HTMLInputElement;
    fireFileSelect(input, [file("r.pdf", "application/pdf")]);

    expect(onDoc).toHaveBeenCalledTimes(1);
    expect(onImage).not.toHaveBeenCalled();
  });

  it("rejects unsupported files via onReject with typed reason", () => {
    const onReject = vi.fn();
    renderWithIntl(
      <ComposerAttachControl
        onImageFile={vi.fn()}
        onDocumentFile={vi.fn()}
        onReject={onReject}
        currentImageCount={0}
      />,
    );
    const input = screen.getByLabelText(/attach file/i) as HTMLInputElement;
    fireFileSelect(input, [file("video.mp4", "video/mp4")]);

    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onReject.mock.calls[0][0]).toBe("unsupported_format");
  });

  it("respects the per-message image cap across one multi-file selection", () => {
    // Already 3 attached → the 1st image in the new selection is the
    // 4th (accepted), the 2nd is the 5th (rejected with per_message_image_cap).
    const onImage = vi.fn();
    const onReject = vi.fn();
    renderWithIntl(
      <ComposerAttachControl
        onImageFile={onImage}
        onDocumentFile={vi.fn()}
        onReject={onReject}
        currentImageCount={3}
      />,
    );
    const input = screen.getByLabelText(/attach file/i) as HTMLInputElement;
    fireFileSelect(input, [
      file("a.png", "image/png"),
      file("b.png", "image/png"),
    ]);

    expect(onImage).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onReject.mock.calls[0][0]).toBe("per_message_image_cap");
  });

  it("disables image attach with the deployment-honest tooltip (D-F3-X-no-vision-tooltip-copy)", () => {
    const onReject = vi.fn();
    renderWithIntl(
      <ComposerAttachControl
        onImageFile={vi.fn()}
        onDocumentFile={vi.fn()}
        onReject={onReject}
        currentImageCount={0}
        imageAttachDisabled
      />,
    );
    // When ONLY images are disabled, the control is still enabled
    // (documents can still attach); the tooltip explains why images are off.
    const input = screen.getByLabelText(en.chat.composer.attach.imageDisabled);
    expect(input).toBeDefined();
  });

  it("rejects image upload attempts when imageAttachDisabled (the fail-loud path)", () => {
    const onImage = vi.fn();
    const onReject = vi.fn();
    renderWithIntl(
      <ComposerAttachControl
        onImageFile={onImage}
        onDocumentFile={vi.fn()}
        onReject={onReject}
        currentImageCount={0}
        imageAttachDisabled
      />,
    );
    const input = screen.getByLabelText(
      en.chat.composer.attach.imageDisabled,
    ) as HTMLInputElement;
    fireFileSelect(input, [file("a.png", "image/png")]);

    expect(onImage).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalled();
  });

  it("rejects document upload when documentsDisabled (persona-detail context)", () => {
    const onDoc = vi.fn();
    const onReject = vi.fn();
    renderWithIntl(
      <ComposerAttachControl
        onImageFile={vi.fn()}
        onDocumentFile={onDoc}
        onReject={onReject}
        currentImageCount={0}
        documentsDisabled
      />,
    );
    const input = screen.getByLabelText(
      en.chat.composer.attach.openConversationFirst,
    ) as HTMLInputElement;
    fireFileSelect(input, [file("r.pdf", "application/pdf")]);

    expect(onDoc).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalled();
  });

  it("ARIA label uses i18n strings, NOT raw English (T20 a11y discipline)", () => {
    renderWithIntl(
      <ComposerAttachControl
        onImageFile={vi.fn()}
        onDocumentFile={vi.fn()}
        onReject={vi.fn()}
        currentImageCount={0}
      />,
    );
    const input = screen.getByLabelText(en.chat.composer.attach.label);
    // The string MUST come from the en.json bundle, not a literal in
    // the component source. T20 expands this check across all F3
    // composer ARIA strings.
    expect(input).toBeDefined();
    expect(input.getAttribute("aria-label")).toBe(
      en.chat.composer.attach.label,
    );
  });
});
