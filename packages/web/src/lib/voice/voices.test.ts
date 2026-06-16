import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiError } from "@/lib/api/client";
import { fetchVoices } from "./voices";

describe("fetchVoices", () => {
  const realFetch = global.fetch;
  afterEach(() => {
    global.fetch = realFetch;
  });

  it("GETs /v1/voices with the Bearer token and returns {provider, voices}", async () => {
    let url = "";
    let auth: string | undefined;
    global.fetch = vi.fn(async (u: RequestInfo | URL, init?: RequestInit) => {
      url = String(u);
      auth = (init?.headers as Record<string, string> | undefined)
        ?.Authorization;
      return new Response(
        JSON.stringify({
          provider: "cartesia",
          voices: [
            {
              voice_id: "v1",
              name: "Clara",
              gender: "feminine",
              language: "en",
              description: "warm",
              preview_url: "https://cdn/c.mp3",
            },
          ],
        }),
        { status: 200 },
      );
    }) as unknown as typeof fetch;

    const list = await fetchVoices({ getToken: async () => "jwt-x" });
    expect(url).toBe("http://localhost:8001/v1/voices");
    expect(auth).toBe("Bearer jwt-x");
    expect(list.provider).toBe("cartesia");
    expect(list.voices[0].voice_id).toBe("v1");
    expect(list.voices[0].preview_url).toBe("https://cdn/c.mp3");
  });

  it("throws ApiError on a non-2xx", async () => {
    global.fetch = vi.fn(
      async () => new Response("{}", { status: 500 }),
    ) as unknown as typeof fetch;
    await expect(
      fetchVoices({ getToken: async () => "x" }),
    ).rejects.toMatchObject({ status: 500 } satisfies Partial<ApiError>);
  });
});
