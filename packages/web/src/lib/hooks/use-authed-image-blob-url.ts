"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * F3 (T10) — fetch an authed image from Spec 13's serve endpoint
 * (D-F3-X-image-serve-auth).
 *
 * `<img src>` cannot render `GET /v1/personas/:id/uploads/:ref` directly:
 * the endpoint requires `Authorization: Bearer <jwt>` (auth/deps.py:62-64)
 * and browsers never send Authorization headers on image GETs. So this
 * hook does the fetch with the Bearer token, wraps the response blob in
 * `URL.createObjectURL`, and hands back the object URL the `<img>` tag
 * can render.
 *
 * **Hook discipline (load-bearing per the decision):**
 *   (a) `AbortController` aborts in-flight fetch on unmount or ref-change.
 *   (b) `URL.revokeObjectURL` fires on unmount AND on ref-change.
 *   (c) 404 → null `src` + null `error` so the `<AuthedImage>` consumer
 *       renders a "image unavailable" placeholder.
 *       401 → re-thrown so the Clerk session refresh runs (callers can
 *       branch on this if needed).
 *       5xx → `error` is set so the consumer can render a retry affordance.
 */
export interface AuthedImageBlobUrlState {
  src: string | null;
  loading: boolean;
  /** Set for 5xx (and other unexpected failures); not for 404 / abort. */
  error: Error | null;
}

export function useAuthedImageBlobUrl(
  personaId: string,
  workspacePath: string,
): AuthedImageBlobUrlState {
  const { getToken } = useAuth();
  const [src, setSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError(null);
      setSrc(null);
      try {
        const token = await getToken(
          TEMPLATE ? { template: TEMPLATE } : undefined,
        );
        const res = await fetch(
          `${API}/v1/personas/${encodeURIComponent(personaId)}/uploads/${workspacePath}`,
          {
            signal: controller.signal,
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          },
        );
        if (cancelled) return;

        if (res.status === 404) {
          // Existence-disclosure-safe per D-08-1; consumer renders placeholder.
          setSrc(null);
          setError(null);
          return;
        }
        if (!res.ok) {
          throw new Error(`image fetch ${res.status}`);
        }
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      } catch (e) {
        if (controller.signal.aborted) return;
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();

    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [personaId, workspacePath, getToken]);

  return { src, loading, error };
}
