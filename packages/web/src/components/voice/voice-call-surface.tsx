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

import {
  ArrowLeft,
  Captions,
  Mic,
  MicOff,
  Phone,
  PhoneOff,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/auth";
import { EmptyState } from "@/components/patterns/empty-state";
import { Button, buttonVariants } from "@/components/ui/button";
import { IdentityOrb } from "@/components/voice/identity-orb";
import { VoiceCaptions } from "@/components/voice/voice-captions";
import { personaIdentityStyle } from "@/lib/persona-identity";
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
}: VoiceCallSurfaceProps): React.JSX.Element | null {
  const t = useTranslations("voice");
  const router = useRouter();
  const { getToken } = useAuth();
  const [captionsOn, setCaptionsOn] = useState(true);
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

  // Spec 35: a clean hang-up drops straight back into the conversation — voice
  // + text are one thread, so a "Call ended" card is dead-end noise. `replace`
  // keeps the spent voice route out of history. Honest failure phases
  // (dropped / error) still render their recovery card below.
  useEffect(() => {
    if (state.phase === "ended") {
      router.replace(`/chat/${conversationId}`);
    }
  }, [state.phase, conversationId, router]);

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

  // A clean end is already navigating away (effect above) — render nothing
  // meanwhile so the "Call ended" card never flashes.
  if (state.phase === "ended") return null;

  // Terminal phases (D-V6-5) — render an honest EmptyState instead of a dead
  // orb. `error` carries a typed kind so the copy + the recovery action are
  // specific (retry vs sign-in vs nothing); `dropped` offers reconnect.
  const terminal = buildTerminal();

  return (
    <div className="v-voice" style={personaIdentityStyle(persona)}>
      {/* Identity-tinted backdrop — a soft radial wash in the persona's hue. */}
      <div
        className="v-voice__bg"
        style={{
          background:
            "radial-gradient(50% 50% at 50% 42%, oklch(0.62 0.13 var(--identity-h) / 0.14), transparent 70%)",
        }}
      />
      <Link
        href={`/chat/${conversationId}`}
        aria-label={t("back")}
        className="v-iconbtn absolute top-4 left-4 z-10"
      >
        <ArrowLeft aria-hidden />
      </Link>

      <header className="v-voice__head">
        <div className="v-voice__title">
          {t.rich("callWith", {
            name: persona.name,
            hl: (chunks) => <span className="v-id-underline">{chunks}</span>,
          })}
        </div>
        {persona.role ? (
          <div className="v-voice__role">{persona.role}</div>
        ) : null}
      </header>

      {terminal ? (
        <EmptyState
          className="relative z-[1] w-full max-w-md"
          icon={<Phone className="size-6" aria-hidden />}
          title={terminal.title}
          description={terminal.body}
          action={terminal.action}
        />
      ) : (
        <>
          <div className="v-orb-wrap">
            <IdentityOrb
              persona={{ id: persona.id, name: persona.name }}
              agentState={state.agentState}
              bargeInSignal={state.bargeInSignal}
              getMicLevel={getMicLevel}
              getPersonaLevel={getPersonaLevel}
              avatarUrl={avatarSrc}
              label={stateLabel}
            />
          </div>

          <div className="v-voice__status" aria-live="polite">
            {statusLine}
          </div>

          {captionsOn ? (
            <div className="v-voice__caption">
              <VoiceCaptions
                captions={call.captions}
                personaName={persona.name}
              />
            </div>
          ) : null}

          {state.needsAudioGesture ? (
            <Button
              variant="secondary"
              className="relative z-[1]"
              onClick={() => void enableAudio()}
            >
              {t("enableAudio")}
            </Button>
          ) : null}

          {live ? (
            <div className="v-voice__controls">
              <button
                type="button"
                className="v-voice-ctl"
                onClick={() => void toggleMute()}
                aria-label={state.micActive ? t("mute") : t("unmute")}
                title={state.micActive ? t("mute") : t("unmute")}
                aria-pressed={!state.micActive}
              >
                {state.micActive ? <Mic aria-hidden /> : <MicOff aria-hidden />}
              </button>
              <button
                type="button"
                className="v-voice-ctl v-voice-ctl--end"
                onClick={() => void end()}
                aria-label={t("end")}
                title={t("end")}
              >
                <PhoneOff aria-hidden />
              </button>
              <button
                type="button"
                className="v-voice-ctl"
                onClick={() => setCaptionsOn((c) => !c)}
                aria-label={t("captionsLabel")}
                title={t("captionsLabel")}
                aria-pressed={captionsOn}
              >
                <Captions aria-hidden />
              </button>
            </div>
          ) : null}

          {/* The shared-memory note — voice + text are one thread (D-V6-4). */}
          <div className="relative z-[1] flex items-center gap-2 font-mono text-muted-foreground type-caption normal-case tracking-normal">
            <span className="v-id-dot" />
            {t("memoryNote")}
          </div>
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
    // `ended` (a clean hang-up) is intentionally NOT a terminal card — the
    // effect above navigates back to the conversation instead.
    return null;
  }
}
