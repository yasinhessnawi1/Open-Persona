/**
 * Spec V6 A2 — persona-voice service base URL.
 *
 * The voice service (`POST /v1/voice/token`, `GET /v1/voices`) is a SEPARATE
 * deployment from persona-api (`NEXT_PUBLIC_API_BASE_URL`); the browser's
 * WebRTC client talks to it directly. Dev default is the local persona-voice
 * uvicorn (port 8001); production sets `NEXT_PUBLIC_VOICE_BASE_URL`.
 *
 * The LiveKit WebSocket URL is NOT configured here — it comes back in the
 * token response (`livekit_url`), so the client always connects to the server
 * the token was minted for (dev `ws://localhost:7880`).
 */
export const VOICE_BASE_URL =
  process.env.NEXT_PUBLIC_VOICE_BASE_URL ?? "http://localhost:8001";
