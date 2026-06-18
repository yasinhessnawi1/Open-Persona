/**
 * The stable auth surface both editions implement (Spec 33, D-33-X-auth-facade-surface).
 *
 * Every Clerk touch is isolated behind `@/auth` (client), `@/auth/server`,
 * `@/auth/provider`, and `@/auth/middleware`. The `cloud` variant is Clerk; the
 * `community` variant is a no-auth stub (fixed local owner). The edition is
 * selected at BUILD time via `turbopack.resolveAlias` keyed on `PERSONA_EDITION`
 * (next.config.ts), so a community build never pulls `@clerk/*` into the bundle.
 *
 * Both variants conform to the types here so call sites are edition-agnostic.
 */
import type { ComponentType, ReactNode } from "react";

/** Mint a bearer token for the API (Clerk JWT-template token in cloud; null in community). */
export type GetToken = (options?: {
  template?: string;
}) => Promise<string | null>;

/** The client-side auth state (the subset every call site uses: just the token getter). */
export interface AuthState {
  getToken: GetToken;
}

/** The server-side auth result (owner id + token getter). */
export interface ServerAuth {
  userId: string | null;
  getToken: GetToken;
}

/**
 * Outcome of resolving the server-side API bearer token (`serverAuthToken`).
 *
 * `signedOut` is `true` when the request's session is gone (logout race / stale
 * cookie / no token) so the caller redirects to `/sign-in` instead of crashing.
 * In community there is no sign-in wall, so it is always `false`.
 */
export interface ServerTokenResult {
  signedOut: boolean;
  token: string | null;
}

/** The current user's profile shape the settings page reads. */
export interface CurrentUser {
  primaryEmailAddress?: { emailAddress?: string | null } | null;
  firstName?: string | null;
  lastName?: string | null;
}

/** Wraps the app tree (ClerkProvider in cloud; passthrough in community). */
export type AuthProviderComponent = ComponentType<{ children: ReactNode }>;
