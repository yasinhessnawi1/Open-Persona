"use client";

/**
 * Spec V6 B5 — the call surface: the live-call screen that hosts the orb.
 *
 * The full-surface "call with the persona" view (D-V6-4): the Identity Orb is
 * the hero, with the persona's identity present, honest phase/failure states
 * (D-V6-5), the autoplay "tap to enable audio" affordance, and mute / end
 * controls. It binds the open `conversationId` (voice + text are one thread) and
 * drives everything from the {@link useVoiceCall} hook.
 *
 * This is the minimal-but-real mount point for the eyes-on motion checkpoint —
 * captions (C1) + the richer failure/mobile polish (C3) layer on after.
 */

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useCallback } from "react";
import { Button } from "@/components/ui/button";
import { IdentityOrb } from "@/components/voice/identity-orb";
import { useVoiceCall } from "@/lib/voice/use-voice-call";

const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

export interface VoiceCallSurfaceProps {
  persona: {
    id: string;
    name: string;
    avatarUrl?: string | null;
    role?: string;
  };
  conversationId: string;
}

export function VoiceCallSurface({
  persona,
  conversationId,
}: VoiceCallSurfaceProps): React.JSX.Element {
  const t = useTranslations("voice");
  const { getToken } = useAuth();
  const token = useCallback(
    () => getToken(TEMPLATE ? { template: TEMPLATE } : undefined),
    [getToken],
  );

  const call = useVoiceCall({
    personaId: persona.id,
    conversationId,
    getToken: token,
  });
  const {
    state,
    start,
    end,
    toggleMute,
    enableAudio,
    getMicLevel,
    getPersonaLevel,
  } = call;

  const stateLabel =
    state.agentState === "thinking"
      ? t("thinking")
      : state.agentState === "speaking"
        ? t("speaking")
        : t("listening");

  const live = state.phase === "connected" || state.phase === "reconnecting";
  const canStart =
    state.phase === "idle" ||
    state.phase === "ended" ||
    state.phase === "dropped" ||
    state.phase === "error";

  const statusLine =
    state.phase === "connecting"
      ? t("connecting")
      : state.phase === "reconnecting"
        ? t("reconnecting")
        : state.phase === "dropped"
          ? t("dropped")
          : state.phase === "ended"
            ? t("ended")
            : state.phase === "error"
              ? t("errorTitle")
              : stateLabel;

  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 p-6">
      <header className="text-center">
        <h1 className="font-serif text-xl">
          {t("callWith", { name: persona.name })}
        </h1>
        {persona.role ? (
          <p className="text-sm text-muted-foreground">{persona.role}</p>
        ) : null}
      </header>

      <IdentityOrb
        persona={{ id: persona.id, name: persona.name }}
        agentState={state.agentState}
        bargeInSignal={state.bargeInSignal}
        getMicLevel={getMicLevel}
        getPersonaLevel={getPersonaLevel}
        avatarUrl={persona.avatarUrl}
        label={stateLabel}
      />

      <p aria-live="polite" className="min-h-5 text-sm text-muted-foreground">
        {statusLine}
        {state.phase === "error" && state.error
          ? ` — ${state.error.message}`
          : null}
      </p>

      {state.needsAudioGesture ? (
        <Button variant="secondary" onClick={() => void enableAudio()}>
          {t("enableAudio")}
        </Button>
      ) : null}

      <div className="flex items-center gap-3">
        {canStart ? (
          <Button onClick={() => void start()}>
            {state.phase === "idle" ? t("start") : t("retry")}
          </Button>
        ) : null}

        {live ? (
          <>
            <Button variant="secondary" onClick={() => void toggleMute()}>
              {state.micActive ? t("mute") : t("unmute")}
            </Button>
            <Button variant="destructive" onClick={() => void end()}>
              {t("end")}
            </Button>
          </>
        ) : null}

        {state.phase === "connecting" ? (
          <span className="text-sm text-muted-foreground">
            {t("connecting")}
          </span>
        ) : null}
      </div>

      <Link
        href={`/chat/${conversationId}`}
        className="text-sm text-muted-foreground underline-offset-4 hover:underline"
      >
        {t("back")}
      </Link>
    </div>
  );
}
