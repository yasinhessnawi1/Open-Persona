"use client";

/**
 * Spec V6 B1 + B2 — the Identity Orb: the conversational-state visualisation.
 *
 * The heart of V6 (D-V6-1). One identity-derived orb that renders the persona's
 * three states as ambient motion — never text-to-read, never a busy visualizer:
 *
 *   - **listening**: a calm breathing orb, gently lifted by the USER mic level
 *     (the persona "leans in" as you speak — the D-V6-6 "I'm hearing you" cue).
 *   - **thinking**: breathing holds while an internal highlight slowly ORBITS —
 *     deterministic, audio-decoupled, never a spinner (the thinking-trap fix).
 *   - **speaking**: the orb morphs + rim-glows with the PERSONA TTS level.
 *
 * It is IDENTITY-DERIVED (D-V6-1 binding constraint): the persona's F1 identity
 * colour fills it, and the Spec-29 avatar — when present — is its core. A is the
 * default precisely because it works avatar-or-not.
 *
 * **B2 — the visible barge-in yield (criterion 4)**: when `bargeInSignal` bumps
 * (a REAL V4 `barge_in` transition, reflected — never computed), the orb fires a
 * fast eased collapse of the speaking morph back into the calm listening
 * breathing, so cutting in visibly lands.
 *
 * Audio levels are polled from the hook's getters in this component's OWN rAF —
 * they never pass through React state (D-V6-1 / A3). Reduced motion (criterion
 * 9) drops the rAF entirely for a static, identity-coloured, labelled orb.
 */

import { useEffect, useRef } from "react";
import {
  derivePersonaIdentityColor,
  personaIdentityStyle,
} from "@/lib/persona-identity";
import {
  bargeInCollapse,
  compressAmplitude,
  DEFAULT_ORB_MOTION,
  orbScale,
  smoothAmplitude,
  thinkingSweepDeg,
} from "@/lib/voice/orb-motion";
import type { AgentVisualState } from "@/lib/voice/voice-events";

const IDENTITY_FILL =
  "oklch(var(--identity-l) var(--identity-c) var(--identity-h))";
/** The barge-in collapse window (ms) — the fast eased settle (D-V6-1 ~120–200ms). */
const COLLAPSE_MS = 200;

export interface IdentityOrbProps {
  persona: { id: string; name: string };
  /** The persona-side cue to render (from the call hook's decoded state). */
  agentState: AgentVisualState;
  /** Bumps on each confirmed barge-in — drives the visible yield (B2). */
  bargeInSignal: number;
  /** Poll the user mic level 0..1 (listening lean-in). */
  getMicLevel: () => number;
  /** Poll the persona TTS level 0..1 (speaking morph). */
  getPersonaLevel: () => number;
  /** The Spec-29 avatar, when the persona has one — rendered as the orb's core. */
  avatarUrl?: string | null;
  /** The translated current-state label (parent owns i18n) — announced to AT. */
  label: string;
  /** Orb diameter in px. */
  size?: number;
}

function initialsFor(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function IdentityOrb({
  persona,
  agentState,
  bargeInSignal,
  getMicLevel,
  getPersonaLevel,
  avatarUrl,
  label,
  size = 220,
}: IdentityOrbProps): React.JSX.Element {
  const orbRef = useRef<HTMLDivElement>(null);
  const sweepRef = useRef<HTMLDivElement>(null);
  // Mutable render inputs read inside the rAF without re-subscribing.
  const stateRef = useRef<AgentVisualState>(agentState);
  stateRef.current = agentState;
  const collapseStartRef = useRef<number | null>(null);
  const lastBargeRef = useRef(bargeInSignal);

  // A barge-in bump starts the collapse window (B2). Tracked as an effect so a
  // change between renders is observed exactly once.
  useEffect(() => {
    if (bargeInSignal !== lastBargeRef.current) {
      lastBargeRef.current = bargeInSignal;
      collapseStartRef.current =
        typeof performance !== "undefined" ? performance.now() : 0;
    }
  }, [bargeInSignal]);

  useEffect(() => {
    if (prefersReducedMotion()) return; // static fallback — no rAF

    let raf = 0;
    let smoothed = 0;
    const cfg = DEFAULT_ORB_MOTION;

    const frame = (now: number) => {
      const state = stateRef.current;
      const rawTarget =
        state === "speaking"
          ? getPersonaLevel()
          : state === "listening"
            ? getMicLevel()
            : 0; // thinking: audio-decoupled
      smoothed = smoothAmplitude(smoothed, rawTarget, cfg);
      let amp = compressAmplitude(smoothed, cfg);

      // B2 — during the barge-in collapse window, ease the morph out fast.
      const cs = collapseStartRef.current;
      if (cs !== null) {
        const progress = (now - cs) / COLLAPSE_MS;
        if (progress >= 1) {
          collapseStartRef.current = null;
        } else {
          amp *= bargeInCollapse(progress);
        }
      }

      const orb = orbRef.current;
      if (orb) {
        orb.style.transform = `scale(${orbScale(state, amp, now, cfg)})`;
        // rim glow tracks amplitude; brightest while speaking.
        const glow = 0.25 + amp * 0.75;
        orb.style.boxShadow = `0 0 ${12 + amp * 48}px ${
          amp * 8
        }px color-mix(in oklch, ${IDENTITY_FILL} ${Math.round(glow * 100)}%, transparent)`;
      }
      const sweep = sweepRef.current;
      if (sweep) {
        // The thinking orbit — visible only while thinking; deterministic.
        sweep.style.opacity = state === "thinking" ? "0.9" : "0";
        sweep.style.transform = `rotate(${thinkingSweepDeg(now, cfg)}deg)`;
      }
      raf = requestAnimationFrame(frame);
    };

    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [getMicLevel, getPersonaLevel]);

  const identityColor = derivePersonaIdentityColor(persona);

  return (
    <div
      className="relative grid place-items-center"
      style={{ ...personaIdentityStyle(persona), width: size, height: size }}
    >
      <div
        ref={orbRef}
        className="relative grid place-items-center rounded-full will-change-transform motion-reduce:shadow-none"
        style={{
          width: size * 0.74,
          height: size * 0.74,
          background: `radial-gradient(circle at 38% 32%, color-mix(in oklch, ${IDENTITY_FILL} 92%, white) 0%, ${IDENTITY_FILL} 58%, color-mix(in oklch, ${IDENTITY_FILL} 70%, black) 100%)`,
          // Reduced-motion static fallback glow (replaced each frame by the rAF
          // when motion is allowed).
          boxShadow: `0 0 16px 2px color-mix(in oklch, ${IDENTITY_FILL} 35%, transparent)`,
        }}
      >
        {/* The thinking orbit highlight — audio-decoupled, never a spinner. */}
        <div
          ref={sweepRef}
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-full opacity-0 motion-reduce:hidden"
          style={{
            background: `conic-gradient(from 0deg, transparent 0deg, color-mix(in oklch, ${IDENTITY_FILL} 85%, white) 28deg, transparent 60deg)`,
            mask: "radial-gradient(circle, transparent 58%, black 60%)",
            WebkitMask: "radial-gradient(circle, transparent 58%, black 60%)",
          }}
        />
        {/* The identity core: the Spec-29 avatar when present, else initials. */}
        {avatarUrl ? (
          // biome-ignore lint/performance/noImgElement: a remote LiveKit-call avatar, not a static asset
          <img
            src={avatarUrl}
            alt=""
            aria-hidden
            className="h-[58%] w-[58%] rounded-full object-cover"
          />
        ) : (
          <span
            aria-hidden
            className="font-serif text-2xl font-medium"
            style={{
              color: `color-mix(in oklch, ${identityColor.oklch} 30%, white)`,
            }}
          >
            {initialsFor(persona.name)}
          </span>
        )}
      </div>
      {/* The accessibility floor: state announced politely; not the visual cue. */}
      <output aria-live="polite" className="sr-only">
        {label}
      </output>
    </div>
  );
}
