import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiError } from "@/lib/api/client";
import { fetchVoices, type VoiceSummary, voiceDisplayName } from "./voices";

function voice(over: Partial<VoiceSummary>): VoiceSummary {
  return {
    voice_id: "v1",
    name: "Kari - Crisp Coordinator",
    gender: "feminine",
    language: "no",
    description: "Polished Norwegian voice.",
    preview_url: null,
    ...over,
  };
}

describe("voiceDisplayName", () => {
  it("strips the contradicting human name to the role descriptor", () => {
    expect(voiceDisplayName(voice({ name: "Kari - Crisp Coordinator" }))).toBe(
      "Crisp Coordinator",
    );
    expect(
      voiceDisplayName(voice({ name: "Lars - Casual Conversationalist" })),
    ).toBe("Casual Conversationalist");
  });

  it("falls back to a gender label for a bare first name (no descriptor)", () => {
    expect(voiceDisplayName(voice({ name: "Kari", gender: "feminine" }))).toBe(
      "Feminine voice",
    );
    expect(voiceDisplayName(voice({ name: "Lars", gender: "masculine" }))).toBe(
      "Masculine voice",
    );
  });

  it("keeps a name with no human prefix and no usable gender", () => {
    expect(voiceDisplayName(voice({ name: "Narrator", gender: "" }))).toBe(
      "Narrator",
    );
  });
});

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
