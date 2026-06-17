"use client";

/**
 * App-sidebar UI-state persistence.
 *
 * Persisted UI chrome (the sidebar width + collapsed flag) must survive a
 * reload but MUST NOT cause a hydration mismatch: the server has no
 * `localStorage`, so the server-rendered tree and the first client paint have
 * to agree on the SAME value. `useSyncExternalStore` is exactly the right
 * primitive — its `getServerSnapshot` is used for SSR *and* the first client
 * render, then React re-renders with the real client snapshot after hydration
 * (a normal post-hydration update, not a mismatch).
 *
 * The store is a tiny localStorage-backed external store with a `storage`-event
 * subscription so two tabs (or the mobile sheet + desktop rail) stay coherent.
 */

import { useCallback, useSyncExternalStore } from "react";

/** Parse a persisted raw string into `T`, returning `fallback` on any failure. */
type Parse<T> = (raw: string) => T;
/** Serialise `T` to the string persisted in localStorage. */
type Serialize<T> = (value: T) => string;

export interface PersistedStateOptions<T> {
  /** The SSR + first-paint value. Must be deterministic (no `window`). */
  readonly fallback: T;
  /** Defaults to JSON.parse; override for primitives that need clamping. */
  readonly parse?: Parse<T>;
  /** Defaults to JSON.stringify. */
  readonly serialize?: Serialize<T>;
}

function readStore<T>(key: string, fallback: T, parse: Parse<T>): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return fallback;
    return parse(raw);
  } catch {
    // Private-mode / quota / blocked storage → behave as if unset.
    return fallback;
  }
}

/**
 * A persisted, SSR-safe piece of UI state.
 *
 * Returns the current value plus a setter that writes through to
 * localStorage and notifies every subscriber (this tab + others).
 */
export function usePersistedState<T>(
  key: string,
  { fallback, parse, serialize }: PersistedStateOptions<T>,
): readonly [T, (next: T) => void] {
  const doParse = parse ?? ((raw: string) => JSON.parse(raw) as T);
  const doSerialize = serialize ?? ((value: T) => JSON.stringify(value));

  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined") return () => {};
      const onStorage = (event: StorageEvent) => {
        if (event.key === key || event.key === null) onChange();
      };
      window.addEventListener("storage", onStorage);
      window.addEventListener(LOCAL_EVENT, onChange);
      return () => {
        window.removeEventListener("storage", onStorage);
        window.removeEventListener(LOCAL_EVENT, onChange);
      };
    },
    [key],
  );

  const getSnapshot = useCallback(
    () => readStore(key, fallback, doParse),
    // doParse/fallback are stable per call-site; key identifies the slot.
    // Recomputed each render is cheap (one localStorage read) and the value
    // is referentially compared by useSyncExternalStore.
    [key, fallback, doParse],
  );

  // SSR + first client paint use the deterministic fallback → no mismatch.
  const getServerSnapshot = useCallback(() => fallback, [fallback]);

  const raw = useSyncExternalStore<T>(
    subscribe,
    // useSyncExternalStore expects a stable identity for the snapshot; the
    // primitive value it returns (number/boolean/string) is compared by value.
    getSnapshot,
    getServerSnapshot,
  );

  const setValue = useCallback(
    (next: T) => {
      if (typeof window === "undefined") return;
      try {
        window.localStorage.setItem(key, doSerialize(next));
      } catch {
        // Best-effort persistence; in-memory state still updates via the event.
      }
      // Same-tab subscribers don't get the native `storage` event — dispatch
      // our own so this component (and the mobile sheet) re-read immediately.
      window.dispatchEvent(new Event(LOCAL_EVENT));
    },
    [key, doSerialize],
  );

  return [raw, setValue] as const;
}

/** Same-tab change channel (the native `storage` event only fires cross-tab). */
const LOCAL_EVENT = "persona:persisted-state";
