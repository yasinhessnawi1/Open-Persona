"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * Spec 28 — fetch a text-based workspace artifact (markdown / code / csv / json
 * / html / mermaid / dot / plaintext) from the existing
 * `GET /v1/personas/:id/uploads/:ref` route (D-28-10 — reuse, no new endpoint).
 *
 * Text sibling of {@link useAuthedImageBlobUrl}: same Bearer-auth fetch + abort
 * discipline, but resolves the response as text for the right-panel renderers.
 * 404 → null text (consumer renders "unavailable"); 5xx → `error`.
 */
export interface AuthedArtifactTextState {
  text: string | null;
  loading: boolean;
  error: Error | null;
}

export function useAuthedArtifactText(
  personaId: string,
  workspacePath: string,
): AuthedArtifactTextState {
  const { getToken } = useAuth();
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError(null);
      setText(null);
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
          setText(null);
          setError(null);
          return;
        }
        if (!res.ok) throw new Error(`artifact fetch ${res.status}`);
        const body = await res.text();
        if (cancelled) return;
        setText(body);
      } catch (e) {
        if (controller.signal.aborted || cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [personaId, workspacePath, getToken]);

  return { text, loading, error };
}
