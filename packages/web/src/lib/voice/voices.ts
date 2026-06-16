/**
 * Spec V6 C2 — voice-catalogue client.
 *
 * Fetches the provider voice catalogue from `GET /v1/voices` (persona-voice) for
 * the voice-selector-with-preview. Mirrors `token.ts` (Bearer auth, ApiError
 * discipline). The response carries the catalogue `provider` so the selector can
 * set the persona's full `VoiceSpec` (`{provider, voice_id}`); `provider` is null
 * (and `voices` empty) when TTS is unconfigured.
 */

import {
  ApiError,
  type ApiErrorBody,
  readRateLimit,
  type TokenGetter,
} from "@/lib/api/client";
import { VOICE_BASE_URL } from "./config";

/** One voice from the provider catalogue (mirrors VoiceCatalogueEntry). */
export interface VoiceSummary {
  voice_id: string;
  name: string;
  gender: string;
  language: string | null;
  description: string | null;
  /** Provider-hosted sample audio — the selector's hear-before-choosing. */
  preview_url: string | null;
}

export interface VoiceList {
  /** The catalogue provider (e.g. `cartesia`); null when TTS is unconfigured. */
  provider: string | null;
  voices: VoiceSummary[];
}

export interface FetchVoicesOptions {
  getToken: TokenGetter;
  signal?: AbortSignal;
}

/**
 * Fetch the voice catalogue. Resolves with {@link VoiceList}; throws
 * {@link ApiError} on a non-2xx. The server already degrades an unconfigured /
 * failing provider to `{provider: null, voices: []}`, so a thrown error here is
 * a genuine transport/auth failure for the caller to surface.
 */
export async function fetchVoices(
  options: FetchVoicesOptions,
): Promise<VoiceList> {
  const jwt = await options.getToken();
  const response = await fetch(`${VOICE_BASE_URL}/v1/voices`, {
    headers: jwt ? { Authorization: `Bearer ${jwt}` } : {},
    signal: options.signal,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => undefined)) as
      | ApiErrorBody
      | undefined;
    throw new ApiError(response.status, body, readRateLimit(response.headers));
  }
  return (await response.json()) as VoiceList;
}
