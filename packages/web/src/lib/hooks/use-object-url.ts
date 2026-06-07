"use client";

import { useEffect, useState } from "react";

/**
 * F3 (T09) — `URL.createObjectURL(file)` with full cleanup discipline
 * (D-F3-X-preview-cleanup-discipline).
 *
 * Wraps a single browser `File` (or `null`) so callers never call
 * `URL.createObjectURL` directly. Revokes the URL on:
 *
 *   1. Unmount (component-tree teardown / route change).
 *   2. File change (a new URL is minted for the new file; the old one
 *      is revoked atomically).
 *   3. Explicit `null` (the caller removed the attachment from state).
 *
 * Returns `null` when `file` is `null`. The returned URL is stable across
 * renders for the same file reference.
 *
 * Distinct from `useAuthedImageBlobUrl` (T10) — that hook owns the
 * server-fetch lifecycle for *already-uploaded* images via Bearer-auth
 * fetch. Two clean, non-overlapping lifecycles.
 */
export function useObjectURL(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (file === null) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [file]);

  return url;
}
