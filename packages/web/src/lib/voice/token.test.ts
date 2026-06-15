import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiError } from "@/lib/api/client";
import { fetchVoiceToken } from "./token";

describe("fetchVoiceToken", () => {
  const realFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    global.fetch = realFetch;
  });

  it("POSTs persona+conversation with a Bearer token and maps the snake_case response", async () => {
    let capturedUrl = "";
    let capturedInit: RequestInit | undefined;
    global.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      capturedUrl = String(url);
      capturedInit = init;
      return new Response(
        JSON.stringify({
          token: "lk.jwt",
          room_name: "persona:abc",
          livekit_url: "ws://localhost:7880",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof fetch;

    const result = await fetchVoiceToken({
      personaId: "p1",
      conversationId: "c1",
      getToken: async () => "jwt-x",
    });

    expect(capturedUrl).toBe("http://localhost:8001/v1/voice/token");
    expect(capturedInit?.method).toBe("POST");
    const headers = capturedInit?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer jwt-x");
    expect(JSON.parse(String(capturedInit?.body))).toEqual({
      persona_id: "p1",
      conversation_id: "c1",
    });
    expect(result).toEqual({
      token: "lk.jwt",
      roomName: "persona:abc",
      livekitUrl: "ws://localhost:7880",
    });
  });

  it("omits the Authorization header when no JWT is available", async () => {
    let headers: Record<string, string> = {};
    global.fetch = vi.fn(
      async (_url: RequestInfo | URL, init?: RequestInit) => {
        headers = (init?.headers as Record<string, string>) ?? {};
        return new Response(
          JSON.stringify({ token: "t", room_name: "r", livekit_url: "u" }),
          { status: 200 },
        );
      },
    ) as typeof fetch;

    await fetchVoiceToken({
      personaId: "p1",
      conversationId: "c1",
      getToken: async () => null,
    });

    expect("Authorization" in headers).toBe(false);
  });

  it("throws ApiError carrying the status + structured body on a non-2xx", async () => {
    global.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ error: "credits_exhausted", detail: "no credits" }),
          { status: 402 },
        ),
    ) as typeof fetch;

    await expect(
      fetchVoiceToken({
        personaId: "p1",
        conversationId: "c1",
        getToken: async () => "jwt-x",
      }),
    ).rejects.toMatchObject({ status: 402 } satisfies Partial<ApiError>);
  });
});
