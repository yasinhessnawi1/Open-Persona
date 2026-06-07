import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useObjectURL } from "./use-object-url";

/**
 * Vitest jsdom doesn't implement URL.createObjectURL by default.
 * We patch both create/revoke per test to assert lifecycle transitions.
 */

describe("useObjectURL — D-F3-X-preview-cleanup-discipline", () => {
  let createCalls: File[];
  let revokeCalls: string[];
  let nextId: number;

  beforeEach(() => {
    createCalls = [];
    revokeCalls = [];
    nextId = 0;
    globalThis.URL.createObjectURL = vi.fn((file: File) => {
      createCalls.push(file);
      nextId += 1;
      return `blob:fake-${nextId}`;
    });
    globalThis.URL.revokeObjectURL = vi.fn((url: string) => {
      revokeCalls.push(url);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns null when file is null", () => {
    const { result } = renderHook(() => useObjectURL(null));
    expect(result.current).toBeNull();
    expect(createCalls).toHaveLength(0);
  });

  it("creates an object URL on mount and revokes on unmount", () => {
    const file = new File([new Uint8Array(10)], "a.png", { type: "image/png" });
    const { result, unmount } = renderHook(({ f }) => useObjectURL(f), {
      initialProps: { f: file },
    });
    expect(result.current).toBe("blob:fake-1");
    expect(createCalls).toEqual([file]);
    expect(revokeCalls).toEqual([]);

    unmount();
    expect(revokeCalls).toEqual(["blob:fake-1"]);
  });

  it("revokes the OLD url when the file changes (new url minted)", () => {
    const fileA = new File([new Uint8Array(10)], "a.png", {
      type: "image/png",
    });
    const fileB = new File([new Uint8Array(20)], "b.png", {
      type: "image/png",
    });
    const { rerender, result } = renderHook(
      ({ f }: { f: File | null }) => useObjectURL(f),
      {
        initialProps: { f: fileA },
      },
    );
    expect(result.current).toBe("blob:fake-1");

    rerender({ f: fileB });
    expect(result.current).toBe("blob:fake-2");
    // The OLD url was revoked on cleanup; the NEW one stays alive.
    expect(revokeCalls).toEqual(["blob:fake-1"]);
  });

  it("revokes the url when file transitions to null", () => {
    const file = new File([new Uint8Array(10)], "a.png", { type: "image/png" });
    const { rerender, result } = renderHook(
      ({ f }: { f: File | null }) => useObjectURL(f),
      { initialProps: { f: file as File | null } },
    );
    expect(result.current).toBe("blob:fake-1");

    rerender({ f: null });
    expect(result.current).toBeNull();
    expect(revokeCalls).toEqual(["blob:fake-1"]);
  });
});
