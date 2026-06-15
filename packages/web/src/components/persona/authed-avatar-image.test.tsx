/**
 * Tests for <AuthedAvatarImage> — the authed render path for internal
 * (Bearer-protected) persona avatars. The blob-fetch hook is mocked so the
 * test asserts the component's branching (fallback while no src; <img> once
 * the blob URL resolves) without Clerk / network.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockHook = vi.fn();
vi.mock("@/lib/hooks/use-authed-image-blob-url", () => ({
  useAuthedImageBlobUrl: (personaId: string, workspacePath: string) =>
    mockHook(personaId, workspacePath),
}));

import { AuthedAvatarImage } from "./authed-avatar-image";

const fallback = <span data-testid="fallback">AS</span>;

describe("AuthedAvatarImage", () => {
  beforeEach(() => mockHook.mockReset());

  it("renders the fallback (initials) while loading (no src yet)", () => {
    mockHook.mockReturnValue({ src: null, loading: true, error: null });
    render(
      <AuthedAvatarImage
        personaId="p1"
        workspacePath="uploads/x.png"
        fallback={fallback}
      />,
    );
    expect(screen.getByTestId("fallback")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("renders the fallback on 404 / error (still no src)", () => {
    mockHook.mockReturnValue({
      src: null,
      loading: false,
      error: new Error("image fetch 500"),
    });
    render(
      <AuthedAvatarImage
        personaId="p1"
        workspacePath="uploads/x.png"
        fallback={fallback}
      />,
    );
    expect(screen.getByTestId("fallback")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("renders an <img> with the resolved blob URL once it loads", () => {
    mockHook.mockReturnValue({
      src: "blob:http://x/abc",
      loading: false,
      error: null,
    });
    render(
      <AuthedAvatarImage
        personaId="p1"
        workspacePath="uploads/x.png"
        alt="avatar"
        fallback={fallback}
      />,
    );
    const img = document.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe("blob:http://x/abc");
    expect(screen.queryByTestId("fallback")).not.toBeInTheDocument();
  });

  it("passes personaId + workspacePath through to the hook", () => {
    mockHook.mockReturnValue({ src: null, loading: true, error: null });
    render(
      <AuthedAvatarImage
        personaId="persona_42"
        workspacePath="uploads/abc.png"
        fallback={fallback}
      />,
    );
    expect(mockHook).toHaveBeenCalledWith("persona_42", "uploads/abc.png");
  });
});
