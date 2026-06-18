import createClient, { type Client, type Middleware } from "openapi-fetch";
import type { paths } from "./schema";

// The committed, typed REST client for persona-api (D-09-1). REST calls go
// through this — never hand-written fetch. SSE is separate (src/lib/sse.ts).

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Async token source — wired to Clerk's getToken({template}) in T03. */
export type TokenGetter = () => Promise<string | null | undefined>;

/** Rate-limit headers the API sends on every response (surface in the UI). */
export interface RateLimit {
  limit: number | null;
  remaining: number | null;
  reset: number | null;
  retryAfter: number | null;
}

function num(value: string | null): number | null {
  if (value === null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function readRateLimit(headers: Headers): RateLimit {
  return {
    limit: num(headers.get("X-RateLimit-Limit")),
    remaining: num(headers.get("X-RateLimit-Remaining")),
    reset: num(headers.get("X-RateLimit-Reset")),
    retryAfter: num(headers.get("Retry-After")),
  };
}

/** The API's structured error body: {"error": "<code>", "detail": ..., "context"?}. */
export interface ApiErrorBody {
  error?: string;
  detail?: unknown;
  context?: Record<string, string>;
}

/** Thrown by {@link unwrap} on any non-2xx response so TanStack Query rejects. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;
  readonly context: Record<string, string> | undefined;
  readonly rateLimit: RateLimit;

  constructor(
    status: number,
    body: ApiErrorBody | undefined,
    rateLimit: RateLimit,
  ) {
    const code = body?.error ?? "error";
    super(`API ${status} (${code})`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = body?.detail;
    this.context = body?.context;
    this.rateLimit = rateLimit;
  }

  /** True for 429 — the caller can show Retry-After. */
  get isRateLimited(): boolean {
    return this.status === 429;
  }
}

/**
 * Build a typed API client. Pass a {@link TokenGetter} to inject
 * `Authorization: Bearer <jwt>` on every request (Clerk; T03). Omit it for
 * unauthenticated calls (e.g. health).
 */
export function createApiClient(getToken?: TokenGetter): Client<paths> {
  const client = createClient<paths>({ baseUrl: BASE_URL });
  if (getToken) {
    const auth: Middleware = {
      async onRequest({ request }) {
        // Defensive: a token getter can throw when the session is gone (Clerk's
        // `getToken()` raises "Session not found" during a logout race). Never
        // let that escape the fetch as an unhandled 500 — send the request
        // unauthenticated instead, so the API answers 401 and the caller can
        // handle it (the server path redirects to /sign-in upstream).
        let token: string | null | undefined;
        try {
          token = await getToken();
        } catch {
          token = null;
        }
        if (token) request.headers.set("Authorization", `Bearer ${token}`);
        return request;
      },
    };
    client.use(auth);
  }
  return client;
}

/**
 * Unwrap an openapi-fetch result: return `data` on 2xx, else throw {@link ApiError}
 * carrying the structured body + rate-limit headers. Use in hooks/server calls so
 * non-2xx becomes a rejection. (204 No Content returns `undefined`.)
 */
export async function unwrap<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): Promise<T> {
  if (result.error !== undefined) {
    throw new ApiError(
      result.response.status,
      result.error as ApiErrorBody,
      readRateLimit(result.response.headers),
    );
  }
  return result.data as T;
}
