/**
 * Spec V6 B1 — the Identity Orb motion math (pure; the unit-tested core).
 *
 * The locked D-V6-1 principles, as pure functions the orb's rAF loop drives:
 *
 *   - **Amplitude-source disambiguates**: the caller feeds the user mic level
 *     while *listening*, the persona TTS level while *speaking*, and 0 while
 *     *thinking* (audio-decoupled). This module never reads audio — it only
 *     shapes a 0..1 level into a calm orb scale/glow.
 *   - **Calm-vs-busy is won here**: {@link smoothAmplitude} is an asymmetric
 *     fast-attack / slow-decay envelope; {@link compressAmplitude} applies a
 *     noise-floor + compressive curve + tight clamp. Jitter is what reads as
 *     "busy"; this smoothing is what makes it read "alive".
 *   - **The thinking-trap fix**: {@link thinkingSweepDeg} is a deterministic,
 *     audio-decoupled, smoothly-looping rotation — never a spinner, never
 *     frozen. {@link breathScale} keeps listening/thinking gently alive.
 *
 * The exact numbers are an EYES-ON Phase-4 checkpoint (D-V6-1 reversibility) —
 * these are principled defaults to tune against the running call, not the final
 * word.
 */

import type { AgentVisualState } from "./voice-events";

export interface OrbMotionConfig {
  /** Fast attack: how quickly the orb rises toward a louder level (0..1 per frame). */
  attack: number;
  /** Slow decay: how slowly it falls toward quiet (0..1 per frame) — < attack. */
  decay: number;
  /** Below this raw level the orb reads as still (room hiss → no motion). */
  noiseFloor: number;
  /** Compressive exponent (<1 lifts quiet detail without letting peaks dominate). */
  compress: number;
  /** Per-state peak amplitude added to the base scale (tight clamp keeps it calm). */
  range: Record<AgentVisualState, number>;
  /** Breathing depth (slow idle scale wobble for listening/thinking). */
  breathDepth: number;
  /** Breathing period (ms). */
  breathPeriodMs: number;
  /** Thinking sweep period (ms) — one full orbit of the internal highlight. */
  thinkPeriodMs: number;
}

export const DEFAULT_ORB_MOTION: OrbMotionConfig = {
  attack: 0.35,
  decay: 0.08,
  noiseFloor: 0.04,
  compress: 0.6,
  range: { listening: 0.06, thinking: 0.0, speaking: 0.14 },
  breathDepth: 0.02,
  breathPeriodMs: 3600,
  thinkPeriodMs: 1800,
};

const clamp01 = (x: number): number => (x < 0 ? 0 : x > 1 ? 1 : x);

/**
 * Asymmetric fast-attack / slow-decay envelope follower. Rises promptly toward a
 * louder `target`, falls slowly toward a quieter one — so the orb tracks speech
 * energy without flickering between syllables. This is the single most important
 * "calm not busy" lever (D-V6-1).
 */
export function smoothAmplitude(
  prev: number,
  target: number,
  cfg: OrbMotionConfig = DEFAULT_ORB_MOTION,
): number {
  const rate = target > prev ? cfg.attack : cfg.decay;
  return prev + (target - prev) * rate;
}

/**
 * Shape a smoothed level into the orb's displayed amplitude: subtract the noise
 * floor, re-normalise, apply the compressive curve, clamp to 0..1. Below the
 * floor the orb is still.
 */
export function compressAmplitude(
  smoothed: number,
  cfg: OrbMotionConfig = DEFAULT_ORB_MOTION,
): number {
  const above = (smoothed - cfg.noiseFloor) / (1 - cfg.noiseFloor);
  return clamp01(above) ** cfg.compress;
}

/**
 * The slow idle "breathing" offset for the scale, in [-breathDepth, +breathDepth].
 * Keeps listening + thinking gently alive even at zero amplitude. Returns 0 for
 * speaking (the audio morph carries it there).
 */
export function breathScale(
  state: AgentVisualState,
  nowMs: number,
  cfg: OrbMotionConfig = DEFAULT_ORB_MOTION,
): number {
  if (state === "speaking") return 0;
  return Math.sin((nowMs / cfg.breathPeriodMs) * Math.PI * 2) * cfg.breathDepth;
}

/**
 * The orb's overall scale: 1 + breathing + amplitude×per-state-range. `amp` is the
 * compressed 0..1 amplitude (0 for thinking). Tight by construction — the range
 * caps keep it calm.
 */
export function orbScale(
  state: AgentVisualState,
  amp: number,
  nowMs: number,
  cfg: OrbMotionConfig = DEFAULT_ORB_MOTION,
): number {
  return 1 + breathScale(state, nowMs, cfg) + amp * cfg.range[state];
}

/**
 * The thinking highlight's rotation in degrees — a deterministic, audio-decoupled,
 * smoothly-looping orbit (the thinking-trap fix). Continuous across loops.
 */
export function thinkingSweepDeg(
  nowMs: number,
  cfg: OrbMotionConfig = DEFAULT_ORB_MOTION,
): number {
  return ((nowMs / cfg.thinkPeriodMs) * 360) % 360;
}

/**
 * The barge-in yield: while the orb is collapsing back to calm after the user cut
 * in (criterion 4), force the effective amplitude down fast. `progress` is 0..1
 * over the collapse window; returns a multiplier that eases the speaking morph
 * out (a quick settle, not a snap).
 */
export function bargeInCollapse(progress: number): number {
  const p = clamp01(progress);
  // ease-out cubic toward 0 — fast initial drop, gentle settle.
  return (1 - p) ** 3;
}
