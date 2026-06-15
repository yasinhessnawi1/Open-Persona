"use client";

/**
 * Spec V6 — Identity Orb motion reference (dev/audit only, like the other
 * /reference/* showcases). Drives the orb through listening / thinking /
 * speaking with a synthetic audio level + a barge-in trigger, so the motion
 * (calm-vs-busy smoothing, the audio-decoupled thinking orbit, the barge-in
 * collapse) can be judged WITHOUT standing up LiveKit + provider keys. The real
 * call drives the same component from live audio via useVoiceCall.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { IdentityOrb } from "@/components/voice/identity-orb";
import { REPRESENTATIVE_PERSONAS } from "@/lib/persona-identity";
import type { AgentVisualState } from "@/lib/voice/voice-events";

const PERSONAS = REPRESENTATIVE_PERSONAS.slice(0, 3);
const STATES: AgentVisualState[] = ["listening", "thinking", "speaking"];
// A sample portrait (direct data-URL, so it renders without the auth path) to
// demonstrate the avatar-as-orb-core treatment (D-V6-3).
const SAMPLE_AVATAR = `data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><radialGradient id="g" cx="40%" cy="32%"><stop offset="0%" stop-color="#f3d9c0"/><stop offset="100%" stop-color="#b07a52"/></radialGradient></defs><rect width="100" height="100" fill="url(#g)"/><circle cx="50" cy="40" r="18" fill="#fff" opacity="0.92"/><path d="M22 92c0-18 12-28 28-28s28 10 28 28z" fill="#fff" opacity="0.92"/></svg>',
)}`;

export default function VoiceOrbReferencePage() {
  const [state, setState] = useState<AgentVisualState>("listening");
  const [bargeInSignal, setBargeInSignal] = useState(0);
  const [persona, setPersona] = useState(PERSONAS[0]);
  const [withAvatar, setWithAvatar] = useState(false);
  // A synthetic "voice energy" 0..1: a slow envelope × speech-like flutter, so
  // listening/speaking visibly react and thinking visibly does NOT.
  const levelRef = useRef(0);

  useEffect(() => {
    let raf = 0;
    const loop = (t: number) => {
      const envelope = (Math.sin(t / 900) * 0.5 + 0.5) ** 2; // slow swell
      const flutter = Math.abs(Math.sin(t / 70)) * 0.4; // syllable-ish
      levelRef.current = Math.min(1, envelope * 0.7 + flutter);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const getLevel = useCallback(() => levelRef.current, []);

  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center gap-8 p-10">
      <h1 className="font-serif text-xl">Identity Orb — motion reference</h1>

      <IdentityOrb
        persona={persona}
        agentState={state}
        bargeInSignal={bargeInSignal}
        getMicLevel={getLevel}
        getPersonaLevel={getLevel}
        avatarUrl={withAvatar ? SAMPLE_AVATAR : undefined}
        label={state}
        size={260}
      />

      <p className="text-sm text-muted-foreground">
        state: <strong>{state}</strong>
      </p>

      <div className="flex flex-wrap items-center justify-center gap-2">
        {STATES.map((s) => (
          <Button
            key={s}
            variant={s === state ? "default" : "secondary"}
            onClick={() => setState(s)}
          >
            {s}
          </Button>
        ))}
        <Button
          variant="destructive"
          onClick={() => {
            // Barge-in = the persona yields: flip to listening + bump the signal.
            setState("listening");
            setBargeInSignal((n) => n + 1);
          }}
        >
          barge-in
        </Button>
      </div>

      <div className="flex flex-wrap items-center justify-center gap-2">
        {PERSONAS.map((p) => (
          <Button
            key={p.id}
            variant={p.id === persona.id ? "default" : "secondary"}
            size="sm"
            onClick={() => setPersona(p)}
          >
            {p.name}
          </Button>
        ))}
        <Button
          variant={withAvatar ? "default" : "secondary"}
          size="sm"
          onClick={() => setWithAvatar((v) => !v)}
        >
          avatar
        </Button>
      </div>
    </div>
  );
}
