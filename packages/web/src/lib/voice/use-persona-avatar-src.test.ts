import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isDirectAvatarUrl,
  usePersonaAvatarSrc,
} from "./use-persona-avatar-src";

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: async () => "jwt-x" }),
}));

describe("isDirectAvatarUrl", () => {
  it("recognises directly-loadable URLs vs workspace refs", () => {
    expect(isDirectAvatarUrl("https://x/y.png")).toBe(true);
    expect(isDirectAvatarUrl("http://x/y.png")).toBe(true);
    expect(isDirectAvatarUrl("blob:abc")).toBe(true);
    expect(isDirectAvatarUrl("data:image/png;base64,AAAA")).toBe(true);
    expect(isDirectAvatarUrl("uploads/abc123.png")).toBe(false);
  });
});

describe("usePersonaAvatarSrc", () => {
  const realFetch = global.fetch;
  beforeEach(() => {
    global.URL.createObjectURL = vi.fn(() => "blob:fake");
    global.URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => {
    global.fetch = realFetch;
    vi.restoreAllMocks();
  });

  it("passes a direct URL through without fetching", () => {
    const fetchSpy = vi.fn();
    global.fetch = fetchSpy as unknown as typeof fetch;
    const { result } = renderHook(() =>
      usePersonaAvatarSrc("p1", "https://cdn/x.png"),
    );
    expect(result.current).toBe("https://cdn/x.png");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("returns null and never fetches when there is no avatar", () => {
    const fetchSpy = vi.fn();
    global.fetch = fetchSpy as unknown as typeof fetch;
    const { result } = renderHook(() => usePersonaAvatarSrc("p1", null));
    expect(result.current).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("fetches a workspace ref with the Bearer token and yields a blob URL", async () => {
    let capturedUrl = "";
    let capturedAuth: string | undefined;
    global.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      capturedUrl = String(url);
      capturedAuth = (init?.headers as Record<string, string> | undefined)
        ?.Authorization;
      // Minimal Response-shaped object — the hook only reads `ok` + `blob()`;
      // jsdom's Response/blob round-trip is unreliable across versions.
      return {
        ok: true,
        status: 200,
        blob: async () => new Blob(["x"]),
      } as Response;
    }) as unknown as typeof fetch;

    const { result } = renderHook(() =>
      usePersonaAvatarSrc("p1", "uploads/abc123.png"),
    );

    await waitFor(() => expect(result.current).toBe("blob:fake"));
    expect(capturedUrl).toBe(
      "http://localhost:8000/v1/personas/p1/uploads/uploads/abc123.png",
    );
    expect(capturedAuth).toBe("Bearer jwt-x");
  });
});
