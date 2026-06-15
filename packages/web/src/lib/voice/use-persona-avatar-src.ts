"use client";

/**
 * Spec V6 — resolve a persona `avatar_url` to a loadable `<img>` src.
 *
 * Spec 29 auto-generates avatars and stores `avatar_url` as a BARE Bearer-auth
 * workspace ref (`uploads/<blake2b>.png`) served from
 * `GET /v1/personas/:id/uploads/:ref` — a plain `<img src>` cannot load it
 * (the route needs an Authorization header browsers never send on image GETs).
 * Spec 29 shipped no frontend, so generated avatars don't render anywhere yet;
 * this resolves them for the call orb (D-V6-3 — the avatar is the orb's core).
 *
 *   - direct URLs (http/https/blob/data — e.g. a user-supplied avatar) pass
 *     through unchanged, no fetch;
 *   - a bare workspace ref is fetched with the Bearer token (same path as
 *     `useAuthedImageBlobUrl`) and wrapped in an object URL;
 *   - null/empty → null, and NO fetch happens (the no-avatar case stays inert).
 */

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;
const DIRECT_URL = /^(https?:|blob:|data:)/;

/** Whether an avatar_url is a directly-loadable URL (vs a workspace ref). */
export function isDirectAvatarUrl(avatarUrl: string): boolean {
  return DIRECT_URL.test(avatarUrl);
}

export function usePersonaAvatarSrc(
  personaId: string,
  avatarUrl: string | null | undefined,
): string | null {
  const { getToken } = useAuth();
  const [src, setSrc] = useState<string | null>(
    avatarUrl && isDirectAvatarUrl(avatarUrl) ? avatarUrl : null,
  );

  useEffect(() => {
    if (!avatarUrl) {
      setSrc(null);
      return;
    }
    if (isDirectAvatarUrl(avatarUrl)) {
      setSrc(avatarUrl);
      return;
    }

    let cancelled = false;
    let objectUrl: string | null = null;
    const controller = new AbortController();

    (async () => {
      try {
        const token = await getToken(
          TEMPLATE ? { template: TEMPLATE } : undefined,
        );
        const res = await fetch(
          `${API}/v1/personas/${encodeURIComponent(personaId)}/uploads/${avatarUrl}`,
          {
            signal: controller.signal,
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          },
        );
        if (cancelled || !res.ok) return;
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      } catch {
        // Avatar is decorative on the call surface — fall back to the orb's
        // identity fill + initials (D-V6-1 works avatar-or-not). Never throw.
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [personaId, avatarUrl, getToken]);

  return src;
}
