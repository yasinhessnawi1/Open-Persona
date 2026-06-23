"use client";

/**
 * Spec V7 D-V7-6 — the mic input controls.
 *
 * `<MicControl>` is the mic affordance, mode-aware:
 *   - always-listening → a mute toggle (the V6 behaviour).
 *   - push-to-talk → a hold-to-talk button (press-and-hold to open the mic);
 *     works on touch (mobile equivalent) and pointer. The actual mic suppression
 *     is reconciled in the provider — this just drives `pttHeld`.
 *
 * `<InputModeToggle>` flips between the two modes (persisted). Both bind the
 * hoisted session; neither touches `useVoiceCall`.
 */

import { Ear, Hand, Mic, MicOff } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallSession } from "@/lib/voice/call-session-context";

export function MicControl({
  className,
}: {
  className?: string;
}): React.JSX.Element {
  const t = useTranslations("voice");
  const { inputMode, pttHeld, setPttHeld, state, toggleMute } =
    useCallSession();

  if (inputMode === "ptt") {
    const isHoldKey = (key: string) => key === " " || key === "Enter";
    return (
      // Hold-to-talk: the mic is open ONLY while this is held. We capture the
      // pointer on press so the hold is reliable — without capture, a touch
      // pointercancel (or sliding a finger/cursor) would fire a premature release
      // and the button would appear to "do nothing". Release ONLY on pointerup /
      // pointercancel (NOT on leave) so sliding off mid-sentence doesn't cut you
      // off. Space/Enter give the same hold for keyboard users (criterion #7).
      <button
        type="button"
        className={className}
        data-held={pttHeld}
        aria-label={t("ptt.hold")}
        title={t("ptt.hold")}
        aria-pressed={pttHeld}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture?.(e.pointerId);
          setPttHeld(true);
        }}
        onPointerUp={() => setPttHeld(false)}
        onPointerCancel={() => setPttHeld(false)}
        onKeyDown={(e) => {
          if (isHoldKey(e.key) && !e.repeat) {
            e.preventDefault();
            setPttHeld(true);
          }
        }}
        onKeyUp={(e) => {
          if (isHoldKey(e.key)) {
            e.preventDefault();
            setPttHeld(false);
          }
        }}
      >
        {pttHeld ? <Mic aria-hidden /> : <MicOff aria-hidden />}
      </button>
    );
  }

  return (
    <button
      type="button"
      className={className}
      onClick={() => void toggleMute()}
      aria-label={state.micActive ? t("mute") : t("unmute")}
      title={state.micActive ? t("mute") : t("unmute")}
      aria-pressed={!state.micActive}
    >
      {state.micActive ? <Mic aria-hidden /> : <MicOff aria-hidden />}
    </button>
  );
}

export function InputModeToggle({
  className,
}: {
  className?: string;
}): React.JSX.Element {
  const t = useTranslations("voice");
  const { inputMode, setInputMode } = useCallSession();
  const ptt = inputMode === "ptt";

  // The icon shows the CURRENT mode so the user can see which they're in — an Ear
  // for always-listening, a Hand for push-to-talk — and the label says what a tap
  // switches TO. (Same icon in both modes was undiscoverable.)
  return (
    <button
      type="button"
      className={className}
      onClick={() => setInputMode(ptt ? "always" : "ptt")}
      aria-label={ptt ? t("ptt.toAlways") : t("ptt.toPtt")}
      title={ptt ? t("ptt.toAlways") : t("ptt.toPtt")}
      aria-pressed={ptt}
    >
      {ptt ? <Hand aria-hidden /> : <Ear aria-hidden />}
    </button>
  );
}
