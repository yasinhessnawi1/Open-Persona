"use client";

/**
 * Spec V6 A3 — the WebRTC voice-call client hook (the client half of V1).
 *
 * Owns one LiveKit Room per call: token fetch → connect → publish mic → play the
 * persona's audio → decode the data-channel (state + captions) → clean teardown.
 * The connection-state machine + autoplay + mic-permission handling (D-V6-5) and
 * the E3 token refresh (re-fetch + reconnect on a hard drop, so a call outlives
 * the 600s token TTL) all live here.
 *
 * **Audio levels are NOT React state.** The user mic level (D-V6-6 "I'm hearing
 * you") and the persona TTS level (D-V6-1 speaking morph) update at 60fps, so
 * they are exposed as pure getters the orb polls in its OWN rAF — pushing them
 * through `setState` would flood re-renders. React state carries only what
 * changes coarsely: phase, agentState, barge-in signal, mic-active, autoplay.
 *
 * Pure mapping logic lives in `call-state.ts` (unit-tested); this module is the
 * SDK-bound glue, exercised live by the Playwright operator pass (criterion 12).
 */

import {
  ConnectionState,
  createAudioAnalyser,
  DisconnectReason,
  RemoteAudioTrack,
  type RemoteTrack,
  Room,
  RoomEvent,
  Track,
} from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, type TokenGetter } from "@/lib/api/client";
import {
  type CallPhase,
  callErrorForMediaError,
  callErrorForTokenStatus,
  callPhaseForConnectionState,
  INITIAL_CALL_STATE,
  type VoiceCallState,
} from "./call-state";
import { fetchVoiceToken } from "./token";
import { agentVisualState, isBargeIn, parseVoiceEvent } from "./voice-events";

export interface UseVoiceCallOptions {
  personaId: string;
  conversationId: string;
  getToken: TokenGetter;
}

export interface VoiceCall {
  state: VoiceCallState;
  /** Start the call (must be called from a user gesture so audio autoplay unlocks). */
  start: () => Promise<void>;
  /** End the call cleanly and release the mic. */
  end: () => Promise<void>;
  /** Toggle the mic mute. */
  toggleMute: () => Promise<void>;
  /** Unlock audio playback after an autoplay block (call from a user gesture). */
  enableAudio: () => Promise<void>;
  /** Current user mic level 0..1 — the orb polls this (D-V6-6). */
  getMicLevel: () => number;
  /** Current persona TTS level 0..1 — the orb polls this (D-V6-1 speaking). */
  getPersonaLevel: () => number;
}

interface Analyser {
  calculateVolume: () => number;
  cleanup: () => void;
}

interface MintedToken {
  livekitUrl: string;
  token: string;
}

export function useVoiceCall(options: UseVoiceCallOptions): VoiceCall {
  const [state, setState] = useState<VoiceCallState>(INITIAL_CALL_STATE);

  const roomRef = useRef<Room | null>(null);
  const audioElsRef = useRef<HTMLMediaElement[]>([]);
  const micAnalyserRef = useRef<Analyser | null>(null);
  const personaAnalyserRef = useRef<Analyser | null>(null);
  const endedByUserRef = useRef(false);
  const reconnectTriedRef = useRef(false);
  // Keep the latest options accessible inside long-lived SDK callbacks without
  // re-subscribing every render.
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const patch = useCallback((p: Partial<VoiceCallState>) => {
    setState((s) => ({ ...s, ...p }));
  }, []);

  const getMicLevel = useCallback(
    () => micAnalyserRef.current?.calculateVolume() ?? 0,
    [],
  );
  const getPersonaLevel = useCallback(
    () => personaAnalyserRef.current?.calculateVolume() ?? 0,
    [],
  );

  const teardownAudio = useCallback(() => {
    micAnalyserRef.current?.cleanup();
    micAnalyserRef.current = null;
    personaAnalyserRef.current?.cleanup();
    personaAnalyserRef.current = null;
    for (const el of audioElsRef.current) {
      el.pause();
      el.srcObject = null;
      el.remove();
    }
    audioElsRef.current = [];
  }, []);

  const handleData = useCallback((payload: Uint8Array) => {
    const event = parseVoiceEvent(payload);
    if (event === null || event.type !== "state") return;
    // transcript frames feed captions (C1); decoded here, rendered there.
    if (isBargeIn(event)) {
      // Bump the barge-in signal so the orb fires its visible yield off the REAL
      // V4 transition (criterion 4) — we reflect, never compute.
      setState((s) => ({
        ...s,
        agentState: agentVisualState(event.toState),
        bargeInSignal: s.bargeInSignal + 1,
      }));
      return;
    }
    setState((s) => ({ ...s, agentState: agentVisualState(event.toState) }));
  }, []);

  const fetchToken = useCallback(
    (): Promise<MintedToken> =>
      fetchVoiceToken({
        personaId: optionsRef.current.personaId,
        conversationId: optionsRef.current.conversationId,
        getToken: optionsRef.current.getToken,
      }),
    [],
  );

  const connectMicAndAudio = useCallback(
    async (room: Room, token: MintedToken) => {
      await room.connect(token.livekitUrl, token.token);
      // Unlock autoplay inside the same gesture that started the call.
      await room.startAudio().catch(() => undefined);
      await room.localParticipant.setMicrophoneEnabled(true);
      const pub = room.localParticipant.getTrackPublication(
        Track.Source.Microphone,
      );
      const micTrack = pub?.audioTrack;
      if (micTrack) {
        micAnalyserRef.current?.cleanup();
        // cloneTrack lets the level read even while the published track is muted.
        micAnalyserRef.current = createAudioAnalyser(micTrack, {
          cloneTrack: true,
        });
      }
      patch({ micActive: true, needsAudioGesture: !room.canPlaybackAudio });
    },
    [patch],
  );

  const handleDisconnect = useCallback(
    async (room: Room, reason?: DisconnectReason) => {
      const clientInitiated =
        endedByUserRef.current || reason === DisconnectReason.CLIENT_INITIATED;
      if (clientInitiated) {
        teardownAudio();
        patch({ phase: "ended", micActive: false });
        return;
      }
      // E3 — a hard drop: the SDK's own resume reuses the cached token (fine for
      // a brief blip), but a reconnect AFTER the 600s TTL needs a FRESH token.
      // Try exactly one re-fetch + reconnect; on failure, surface "dropped".
      if (!reconnectTriedRef.current) {
        reconnectTriedRef.current = true;
        patch({ phase: "reconnecting" });
        try {
          const token = await fetchToken();
          await connectMicAndAudio(room, token);
          return; // the Connected event restores the phase
        } catch {
          // fall through to dropped
        }
      }
      teardownAudio();
      patch({ phase: "dropped", micActive: false });
    },
    [connectMicAndAudio, fetchToken, patch, teardownAudio],
  );

  const wireRoom = useCallback(
    (room: Room) => {
      room.on(RoomEvent.ConnectionStateChanged, (cs: ConnectionState) => {
        if (cs === ConnectionState.Disconnected) return; // owned by Disconnected
        patch({ phase: callPhaseForConnectionState(cs) });
      });
      room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
        if (!(track instanceof RemoteAudioTrack)) return;
        const el = track.attach();
        el.style.display = "none";
        document.body.appendChild(el);
        audioElsRef.current.push(el);
        personaAnalyserRef.current?.cleanup();
        personaAnalyserRef.current = createAudioAnalyser(track, {});
      });
      room.on(RoomEvent.DataReceived, (payload: Uint8Array) =>
        handleData(payload),
      );
      room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
        patch({ needsAudioGesture: !room.canPlaybackAudio });
      });
      room.on(RoomEvent.Disconnected, (reason?: DisconnectReason) => {
        void handleDisconnect(room, reason);
      });
    },
    [handleData, handleDisconnect, patch],
  );

  const start = useCallback(async () => {
    if (roomRef.current) return;
    endedByUserRef.current = false;
    reconnectTriedRef.current = false;
    patch({ phase: "connecting", error: null });

    let token: MintedToken;
    try {
      token = await fetchToken();
    } catch (err) {
      const error =
        err instanceof ApiError
          ? callErrorForTokenStatus(err.status)
          : {
              kind: "service_unavailable" as const,
              message: "The voice service is unavailable.",
            };
      patch({ phase: "error", error });
      return;
    }

    const room = new Room({
      audioCaptureDefaults: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    roomRef.current = room;
    wireRoom(room);

    try {
      await connectMicAndAudio(room, token);
    } catch (err) {
      patch({ phase: "error", error: callErrorForMediaError(err) });
      endedByUserRef.current = true;
      await room.disconnect().catch(() => undefined);
      teardownAudio();
      roomRef.current = null;
    }
  }, [connectMicAndAudio, fetchToken, patch, teardownAudio, wireRoom]);

  const end = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    endedByUserRef.current = true;
    await room.disconnect().catch(() => undefined);
    teardownAudio();
    roomRef.current = null;
  }, [teardownAudio]);

  const toggleMute = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    const next = !room.localParticipant.isMicrophoneEnabled;
    await room.localParticipant.setMicrophoneEnabled(next);
    patch({ micActive: next });
  }, [patch]);

  const enableAudio = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    await room.startAudio().catch(() => undefined);
    patch({ needsAudioGesture: !room.canPlaybackAudio });
  }, [patch]);

  // Teardown on unmount — a call must never outlive its surface.
  useEffect(() => {
    return () => {
      const room = roomRef.current;
      if (room) {
        endedByUserRef.current = true;
        void room.disconnect().catch(() => undefined);
      }
      teardownAudio();
      roomRef.current = null;
    };
  }, [teardownAudio]);

  return {
    state,
    start,
    end,
    toggleMute,
    enableAudio,
    getMicLevel,
    getPersonaLevel,
  };
}

export type { CallPhase };
