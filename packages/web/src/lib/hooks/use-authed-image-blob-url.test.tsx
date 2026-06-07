import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthedImageBlobUrl } from "./use-authed-image-blob-url";

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({
    getToken: () => Promise.resolve("jwt-token"),
  }),
}));

describe("useAuthedImageBlobUrl — D-F3-X-image-serve-auth", () => {
  let fetchCalls: Array<{ url: string; init?: RequestInit }>;

  beforeEach(() => {
    fetchCalls = [];
    let n = 0;
    globalThis.URL.createObjectURL = vi.fn(() => {
      n += 1;
      return `blob:authed-${n}`;
    });
    globalThis.URL.revokeObjectURL = vi.fn();
    globalThis.fetch = vi.fn(async (url, init) => {
      fetchCalls.push({
        url: typeof url === "string" ? url : url.toString(),
        init,
      });
      // jsdom's Response/Blob constructor is flaky — patch blob() on the
      // response instance directly so res.blob() resolves to a known value.
      const res = new Response(null, { status: 200 });
      Object.defineProperty(res, "blob", {
        value: () =>
          Promise.resolve(
            new Blob([new Uint8Array(10)], { type: "image/png" }),
          ),
      });
      return res;
    }) as unknown as typeof fetch;
  });
  afterEach(() => vi.restoreAllMocks());

  it("fetches with Bearer auth and yields a blob URL", async () => {
    const { result } = renderHook(() =>
      useAuthedImageBlobUrl("persona_abc", "uploads/x.png"),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.src).toMatch(/^blob:authed-/);
    expect(result.current.error).toBeNull();
    expect(fetchCalls[0].url).toContain(
      "/v1/personas/persona_abc/uploads/uploads/x.png",
    );
    expect(
      (fetchCalls[0].init?.headers as Record<string, string>).Authorization,
    ).toBe("Bearer jwt-token");
  });

  it("404 yields null src + null error (existence-disclosure-safe)", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(null, { status: 404 }),
    ) as unknown as typeof fetch;
    const { result } = renderHook(() =>
      useAuthedImageBlobUrl("p", "uploads/missing.png"),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.src).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("5xx sets error so the consumer can render a retry affordance", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(null, { status: 503 }),
    ) as unknown as typeof fetch;
    const { result } = renderHook(() =>
      useAuthedImageBlobUrl("p", "uploads/x.png"),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.src).toBeNull();
  });

  it("revokes the blob URL on unmount (no memory leak)", async () => {
    const { result, unmount } = renderHook(() =>
      useAuthedImageBlobUrl("p", "uploads/x.png"),
    );
    await waitFor(() => expect(result.current.src).not.toBeNull());
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it("aborts the in-flight fetch on unmount (AbortController cleanup)", async () => {
    let abortFired = false;
    globalThis.fetch = vi.fn(
      (_url, init) =>
        new Promise((_, reject) => {
          (init as RequestInit).signal?.addEventListener("abort", () => {
            abortFired = true;
            reject(new Error("aborted"));
          });
        }),
    ) as unknown as typeof fetch;
    const { unmount } = renderHook(() =>
      useAuthedImageBlobUrl("p", "uploads/x.png"),
    );
    // Wait one microtask so the hook reaches fetch() (after getToken await).
    await Promise.resolve();
    await Promise.resolve();
    unmount();
    expect(abortFired).toBe(true);
  });

  it("re-fetches when workspacePath changes (and revokes the old URL)", async () => {
    const { rerender, result } = renderHook(
      ({ path }: { path: string }) => useAuthedImageBlobUrl("p", path),
      { initialProps: { path: "uploads/a.png" } },
    );
    await waitFor(() => expect(result.current.src).not.toBeNull());
    const firstSrc = result.current.src;
    rerender({ path: "uploads/b.png" });
    // Wait for BOTH conditions in one predicate: src is non-null AND
    // different from the original. The hook briefly nulls src during the
    // effect cleanup; checking both together avoids racing the null window.
    await waitFor(() => {
      expect(result.current.src).not.toBeNull();
      expect(result.current.src).not.toBe(firstSrc);
    });
    // The old URL was revoked when the effect cleanup fired on path change.
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(firstSrc);
  });
});
