import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { usePersistedState } from "./use-persisted-state";

afterEach(() => {
  window.localStorage.clear();
});

describe("usePersistedState", () => {
  it("returns the fallback when nothing is persisted", () => {
    const { result } = renderHook(() =>
      usePersistedState("k-empty", { fallback: 42 }),
    );
    expect(result.current[0]).toBe(42);
  });

  it("hydrates from localStorage when a value exists", () => {
    window.localStorage.setItem("k-existing", JSON.stringify(280));
    const { result } = renderHook(() =>
      usePersistedState("k-existing", { fallback: 42 }),
    );
    expect(result.current[0]).toBe(280);
  });

  it("persists and re-reads on set", () => {
    const { result } = renderHook(() =>
      usePersistedState("k-set", { fallback: false }),
    );
    act(() => {
      result.current[1](true);
    });
    expect(result.current[0]).toBe(true);
    expect(window.localStorage.getItem("k-set")).toBe("true");
  });

  it("falls back when the stored value is corrupt", () => {
    window.localStorage.setItem("k-bad", "{not json");
    const { result } = renderHook(() =>
      usePersistedState("k-bad", { fallback: 7 }),
    );
    expect(result.current[0]).toBe(7);
  });

  it("supports a custom parse that clamps (no mismatch path)", () => {
    window.localStorage.setItem("k-clamp", "9999");
    const { result } = renderHook(() =>
      usePersistedState("k-clamp", {
        fallback: 100,
        parse: (raw) => Math.min(400, Math.max(0, Number(raw))),
        serialize: String,
      }),
    );
    expect(result.current[0]).toBe(400);
  });
});
