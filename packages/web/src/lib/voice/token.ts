/**
 * Spec V6 A2 — voice token client.
 *
 * Fetches a LiveKit Room access token from `POST /v1/voice/token` (persona-voice)
 * for an owned persona + conversation. The browser uses the returned token +
 * `livekitUrl` to join the call's Room directly. Mirrors the Bearer-auth +
 * {@link ApiError} discipline of `lib/upload.ts` / `lib/sse.ts` (the API is our
 * own trusted service; cast the JSON after the status check).
 *
 * Error mapping (the token endpoint's fail-closed contract):
 *   - 401 → authentication_error (bad/expired Clerk JWT)
 *   - 402 → credits_exhausted (out of credits — surfaced like chat's 402)
 *   - 404 → persona not found / not owned (RLS-shape; never leaks existence)
 * all carried on {@link ApiError} so the call surface reuses existing handling.
 */

import {
  ApiError,
  type ApiErrorBody,
  readRateLimit,
  type TokenGetter,
} from "@/lib/api/client";
import { VOICE_BASE_URL } from "./config";

/** A minted LiveKit Room access token + where to use it. */
export interface VoiceToken {
  /** The signed LiveKit access JWT (room-scoped; ~600s TTL). */
  token: string;
  /** The Room to join (`persona:{session_id}`). */
  roomName: string;
  /** The LiveKit WebSocket URL to connect to (dev `ws://localhost:7880`). */
  livekitUrl: string;
}

/** Wire shape of the `POST /v1/voice/token` 200 response (snake_case). */
interface TokenResponseWire {
  token: string;
  room_name: string;
  livekit_url: string;
}

export interface FetchVoiceTokenOptions {
  personaId: string;
  conversationId: string;
  /** Async source of the Clerk JWT (the `lib/api/client` TokenGetter). */
  getToken: TokenGetter;
  /** Optional abort signal (call cancelled / unmounted before the fetch resolves). */
  signal?: AbortSignal;
}

/**
 * Fetch a voice token for `personaId` + `conversationId`.
 *
 * Resolves with the {@link VoiceToken}; throws {@link ApiError} on any non-2xx
 * (carrying the structured error body + rate-limit headers).
 */
export async function fetchVoiceToken(
  options: FetchVoiceTokenOptions,
): Promise<VoiceToken> {
  const jwt = await options.getToken();
  const response = await fetch(`${VOICE_BASE_URL}/v1/voice/token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
    },
    body: JSON.stringify({
      persona_id: options.personaId,
      conversation_id: options.conversationId,
    }),
    signal: options.signal,
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => undefined)) as
      | ApiErrorBody
      | undefined;
    throw new ApiError(response.status, body, readRateLimit(response.headers));
  }

  const data = (await response.json()) as TokenResponseWire;
  return {
    token: data.token,
    roomName: data.room_name,
    livekitUrl: data.livekit_url,
  };
}
