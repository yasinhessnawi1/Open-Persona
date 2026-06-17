"use client";

/**
 * Spec V6 B5 + C3 — the call surface: the live-call screen that hosts the orb.
 *
 * The full-surface "call with the persona" view (D-V6-4): the Identity Orb is
 * the hero, with the persona's identity present, honest phase/failure states
 * (D-V6-5), the autoplay "tap to enable audio" affordance, and mute / end
 * controls. It binds the open `conversationId` (voice + text are one thread) and
 * drives everything from the {@link useVoiceCall} hook.
 *
 * C3 layers the **honest failure surface** on top of B5's live view: every
 * terminal phase (pre-connect error, dropped, clean end) renders through F2's
 * `EmptyState` pattern with kind-specific copy + the right recovery affordance
 * (retry / sign-in / call-again), and the layout is responsive for the mobile
 * contexts where voice naturally lives (D-V6-5 criteria 7 + 10).
 */

import { useAuth } from "@clerk/nextjs";
import { Phone } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useCallback, useEffect } from "react";
import { EmptyState } from "@/components/patterns/empty-state";
import { Button, buttonVariants } from "@/components/ui/button";
import { IdentityOrb } from "@/components/voice/identity-orb";
import { VoiceCaptions } from "@/components/voice/voice-captions";
import { usePersonaAvatarSrc } from "@/lib/voice/use-persona-avatar-src";
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

  // Resolve the persona's avatar (a Spec-29 Bearer-auth workspace ref, or a
  // direct URL) to a loadable src so it can be the orb's core (D-V6-3).
  const avatarSrc = usePersonaAvatarSrc(persona.id, persona.avatarUrl);

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

  // The call starts as soon as the surface mounts — reaching here IS the user's
  // "call" click (the chat-header phone control navigated in). No separate
  // "Start call" step. `start()` is idempotent (no-ops if a room exists), and
  // the autoplay-gesture fallback covers the rare case audio needs a tap.
  useEffect(() => {
    void start();
  }, [start]);

  const stateLabel =
    state.agentState === "thinking"
      ? t("thinking")
      : state.agentState === "speaking"
        ? t("speaking")
        : t("listening");

  // `ringing` (Spec 32 greet-first) is a live phase — the orb renders while the
  // persona prepares its greeting; the mic stays gated until the greeting ends.
  const live =
    state.phase === "connected" ||
    state.phase === "reconnecting" ||
    state.phase === "ringing";

  const statusLine =
    state.phase === "connecting"
      ? t("connecting")
      : state.phase === "ringing"
        ? t("ringing", { name: persona.name })
        : state.phase === "reconnecting"
          ? t("reconnecting")
          : stateLabel;

  // Terminal phases (D-V6-5) — render an honest EmptyState instead of a dead
  // orb. `error` carries a typed kind so the copy + the recovery action are
  // specific (retry vs sign-in vs nothing); `dropped`/`ended` offer reconnect.
  const terminal = buildTerminal();

  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 p-4 sm:gap-8 sm:p-6">
      <header className="text-center">
        <h1 className="font-serif text-lg sm:text-xl">
          {t("callWith", { name: persona.name })}
        </h1>
        {persona.role ? (
          <p className="text-sm text-muted-foreground">{persona.role}</p>
        ) : null}
      </header>

      {terminal ? (
        <EmptyState
          className="w-full max-w-md"
          icon={<Phone className="size-6" aria-hidden />}
          title={terminal.title}
          description={terminal.body}
          action={
            <div className="flex flex-wrap items-center justify-center gap-3">
              {terminal.action}
              <Link
                href={`/chat/${conversationId}`}
                className="text-sm text-muted-foreground underline-offset-4 hover:underline"
              >
                {t("back")}
              </Link>
            </div>
          }
        />
      ) : (
        <>
          <IdentityOrb
            persona={{ id: persona.id, name: persona.name }}
            agentState={state.agentState}
            bargeInSignal={state.bargeInSignal}
            getMicLevel={getMicLevel}
            getPersonaLevel={getPersonaLevel}
            avatarUrl={avatarSrc}
            label={stateLabel}
          />

          <p
            aria-live="polite"
            className="min-h-5 text-sm text-muted-foreground"
          >
            {statusLine}
          </p>

          <VoiceCaptions captions={call.captions} personaName={persona.name} />

          {state.needsAudioGesture ? (
            <Button variant="secondary" onClick={() => void enableAudio()}>
              {t("enableAudio")}
            </Button>
          ) : null}

          <div className="flex flex-wrap items-center justify-center gap-3">
            {live ? (
              <>
                <Button
                  variant="secondary"
                  size="lg"
                  onClick={() => void toggleMute()}
                >
                  {state.micActive ? t("mute") : t("unmute")}
                </Button>
                <Button
                  variant="destructive"
                  size="lg"
                  onClick={() => void end()}
                >
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
        </>
      )}
    </div>
  );

  /** Resolve the terminal-phase copy + recovery action, or null if live. */
  function buildTerminal(): {
    title: string;
    body: string;
    action: React.ReactNode;
  } | null {
    if (state.phase === "error" && state.error) {
      const kind = state.error.kind;
      let action: React.ReactNode = null;
      if (kind === "unauthorized") {
        // Re-auth is the only fix — link to sign-in, styled as the primary.
        action = (
          <Link
            href="/sign-in"
            className={buttonVariants({ variant: "default", size: "lg" })}
          >
            {t("signIn")}
          </Link>
        );
      } else if (kind !== "not_found" && kind !== "credits_exhausted") {
        // mic_* / service_unavailable / unknown — retry is meaningful (the user
        // can grant the mic, or the service can recover). not_found + credits
        // can't be retried away, so they get only the back link.
        action = (
          <Button size="lg" onClick={() => void start()}>
            {t("retry")}
          </Button>
        );
      }
      return {
        title: t(`fail.${kind}.title`),
        body: t(`fail.${kind}.body`),
        action,
      };
    }
    if (state.phase === "dropped") {
      return {
        title: t("dropped"),
        body: t("droppedBody"),
        action: (
          <Button size="lg" onClick={() => void start()}>
            {t("retry")}
          </Button>
        ),
      };
    }
    if (state.phase === "ended") {
      return {
        title: t("ended"),
        body: t("endedBody"),
        action: (
          <Button size="lg" onClick={() => void start()}>
            {t("callAgain")}
          </Button>
        ),
      };
    }
    return null;
  }
}
