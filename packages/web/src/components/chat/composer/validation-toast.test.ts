import { describe, expect, it, vi } from "vitest";
import { surfaceValidationFailure } from "./validation-toast";

describe("surfaceValidationFailure — F3 T16 + T17", () => {
  it("emits a single error toast with the detail string", () => {
    const toast = { error: vi.fn() };
    surfaceValidationFailure(
      "oversize",
      "big.png exceeds the 20.0 MB upload limit",
      toast,
      // biome-ignore lint/suspicious/noExplicitAny: test stub
      ((key: string) => key) as any,
    );
    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toast.error.mock.calls[0][0]).toContain("20.0 MB");
  });

  it("surfaces per-message cap message verbatim (T17)", () => {
    const toast = { error: vi.fn() };
    surfaceValidationFailure(
      "per_message_image_cap",
      "You can attach at most 4 images per message",
      toast,
      // biome-ignore lint/suspicious/noExplicitAny: test stub
      ((key: string) => key) as any,
    );
    expect(toast.error.mock.calls[0][0]).toContain("4 images");
  });

  it("surfaces unsupported-format details (T16 — F2 honest voice)", () => {
    const toast = { error: vi.fn() };
    surfaceValidationFailure(
      "unsupported_format",
      "video.mp4 is not a supported format. Accepted: images (...) and documents (...).",
      toast,
      // biome-ignore lint/suspicious/noExplicitAny: test stub
      ((key: string) => key) as any,
    );
    expect(toast.error.mock.calls[0][0]).toContain("video.mp4");
    // F2 voice: NOT "upload failed" — the user sees WHY.
    expect(toast.error.mock.calls[0][0]).not.toBe("upload failed");
  });

  it("surfaces empty-file rejection", () => {
    const toast = { error: vi.fn() };
    surfaceValidationFailure(
      "empty_file",
      "empty.png is empty",
      toast,
      // biome-ignore lint/suspicious/noExplicitAny: test stub
      ((key: string) => key) as any,
    );
    expect(toast.error.mock.calls[0][0]).toContain("empty");
  });
});
