import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api/client";
import { removeDocument } from "./document-actions";

describe("removeDocument — F3 T14", () => {
  let fetchCalls: Array<{ url: string; init?: RequestInit }>;

  beforeEach(() => {
    fetchCalls = [];
    globalThis.fetch = vi.fn(async (url, init) => {
      fetchCalls.push({
        url: typeof url === "string" ? url : (url as Request).url,
        init,
      });
      return new Response(null, { status: 204 });
    }) as unknown as typeof fetch;
  });
  afterEach(() => vi.restoreAllMocks());

  it("DELETEs the correct path with Bearer auth", async () => {
    // Capture the Request as well as the init arg — openapi-fetch may pass
    // the method via either, depending on its internal call shape.
    let capturedMethod: string | undefined;
    let capturedAuth: string | undefined;
    globalThis.fetch = vi.fn(async (url, init) => {
      const req = url instanceof Request ? url : null;
      capturedMethod = req?.method ?? (init?.method as string | undefined);
      capturedAuth =
        req?.headers.get("Authorization") ??
        (init?.headers as Record<string, string> | undefined)?.Authorization;
      return new Response(null, { status: 204 });
    }) as unknown as typeof fetch;

    await removeDocument("conv_1", "doc-1", async () => "jwt-x");
    expect(capturedMethod).toBe("DELETE");
    expect(capturedAuth).toBe("Bearer jwt-x");
  });

  it("204 No Content resolves silently (success path)", async () => {
    await expect(
      removeDocument("conv_1", "doc-1", async () => "jwt-x"),
    ).resolves.toBeUndefined();
  });

  it("404 is idempotent — resolves silently (doc already removed)", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(null, { status: 404 }),
    ) as unknown as typeof fetch;
    await expect(
      removeDocument("conv_1", "doc-gone", async () => "jwt-x"),
    ).resolves.toBeUndefined();
  });

  it("5xx throws ApiError so the caller can refresh + toast", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "internal", detail: "oops" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
    ) as unknown as typeof fetch;
    await expect(
      removeDocument("conv_1", "doc-1", async () => "jwt-x"),
    ).rejects.toThrow(ApiError);
  });
});
