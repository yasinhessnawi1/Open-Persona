/**
 * AvatarEditor — show + replace a persona's avatar on the edit page.
 *
 * Verifies: the avatar preview renders; selecting an image uploads it and reports
 * the returned workspace ref up via onChange; an upload failure surfaces inline.
 */
import { fireEvent, render, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import messages from "@/i18n/messages/en.json";

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: () => Promise.resolve("test-token") }),
}));
const uploadImage = vi.fn();
vi.mock("@/lib/upload", () => ({
  uploadImage: (...args: unknown[]) => uploadImage(...args),
}));

import { AvatarEditor } from "./avatar-editor";

function renderEditor(onChange = vi.fn()) {
  const utils = render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <AvatarEditor
        personaId="p1"
        name="Astrid"
        avatarUrl={null}
        onChange={onChange}
      />
    </NextIntlClientProvider>,
  );
  return { ...utils, onChange };
}

const FILE = new File([new Uint8Array([1, 2, 3])], "face.png", {
  type: "image/png",
});

describe("AvatarEditor", () => {
  it("renders the avatar preview (initials fallback when no avatar)", () => {
    const { container } = renderEditor();
    expect(
      container.querySelector('[data-slot="avatar-editor"]'),
    ).not.toBeNull();
    // Astrid → initials mark.
    expect(container.textContent).toContain("A");
  });

  it("uploads a selected image and reports the workspace ref", async () => {
    uploadImage.mockResolvedValueOnce({
      workspace_path: "uploads/abc.png",
      media_type: "image/png",
      size_bytes: 3,
    });
    const { container, onChange } = renderEditor();
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [FILE] } });
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith("uploads/abc.png"),
    );
    expect(uploadImage).toHaveBeenCalledWith(
      "p1",
      FILE,
      expect.objectContaining({ getToken: expect.any(Function) }),
    );
  });

  it("surfaces an inline error when the upload fails", async () => {
    uploadImage.mockRejectedValueOnce(new Error("boom"));
    const { container, onChange } = renderEditor();
    const input = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [FILE] } });
    await waitFor(() =>
      expect(
        container.querySelector('[data-slot="avatar-error"]'),
      ).not.toBeNull(),
    );
    expect(onChange).not.toHaveBeenCalled();
  });
});
