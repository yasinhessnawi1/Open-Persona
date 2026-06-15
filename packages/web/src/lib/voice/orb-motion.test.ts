import { describe, expect, it } from "vitest";
import {
  bargeInCollapse,
  breathScale,
  compressAmplitude,
  DEFAULT_ORB_MOTION,
  orbScale,
  smoothAmplitude,
  thinkingSweepDeg,
} from "./orb-motion";

describe("smoothAmplitude (fast-attack / slow-decay)", () => {
  it("rises faster than it falls — the calm-not-busy lever", () => {
    const rise = smoothAmplitude(0, 1);
    const fall = smoothAmplitude(1, 0);
    // rise covers more ground toward target than fall does (attack > decay).
    expect(rise).toBeGreaterThan(1 - fall);
  });

  it("is a contraction toward the target (stable, no overshoot)", () => {
    let v = 0;
    for (let i = 0; i < 200; i++) v = smoothAmplitude(v, 1);
    expect(v).toBeGreaterThan(0.99);
    expect(v).toBeLessThanOrEqual(1);
  });
});

describe("compressAmplitude (noise floor + compress + clamp)", () => {
  it("reads room hiss below the noise floor as still", () => {
    expect(compressAmplitude(DEFAULT_ORB_MOTION.noiseFloor - 0.001)).toBe(0);
  });

  it("clamps to 0..1 and lifts quiet detail (compress < 1)", () => {
    const mid = compressAmplitude(0.5);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThanOrEqual(1);
    // a compressive curve lifts a mid input above the linear baseline.
    const linear =
      (0.5 - DEFAULT_ORB_MOTION.noiseFloor) /
      (1 - DEFAULT_ORB_MOTION.noiseFloor);
    expect(mid).toBeGreaterThan(linear);
  });
});

describe("orbScale", () => {
  it("keeps the scale in a tight calm band (never balloons)", () => {
    for (const state of ["listening", "thinking", "speaking"] as const) {
      for (let t = 0; t < DEFAULT_ORB_MOTION.breathPeriodMs; t += 200) {
        const s = orbScale(state, 1, t);
        expect(s).toBeGreaterThan(0.95);
        expect(s).toBeLessThan(1.2);
      }
    }
  });

  it("thinking has no audio amplitude (audio-decoupled) — only breathing moves it", () => {
    // amp is ignored for thinking (range.thinking === 0): scale == 1 + breath.
    const withAmp = orbScale("thinking", 1, 100);
    const noAmp = orbScale("thinking", 0, 100);
    expect(withAmp).toBe(noAmp);
  });
});

describe("breathScale", () => {
  it("is bounded by the breath depth and silent while speaking", () => {
    expect(Math.abs(breathScale("listening", 123))).toBeLessThanOrEqual(
      DEFAULT_ORB_MOTION.breathDepth + 1e-9,
    );
    expect(breathScale("speaking", 123)).toBe(0);
  });
});

describe("thinkingSweepDeg (the never-a-spinner orbit)", () => {
  it("loops smoothly in [0, 360) and advances with time", () => {
    expect(thinkingSweepDeg(0)).toBe(0);
    expect(thinkingSweepDeg(100)).toBeGreaterThan(0);
    expect(thinkingSweepDeg(DEFAULT_ORB_MOTION.thinkPeriodMs)).toBeCloseTo(
      0,
      5,
    );
    expect(thinkingSweepDeg(1e6)).toBeLessThan(360);
  });
});

describe("bargeInCollapse", () => {
  it("eases the speaking morph out fast then settles (1→0)", () => {
    expect(bargeInCollapse(0)).toBe(1);
    expect(bargeInCollapse(1)).toBe(0);
    // fast initial drop: half-way through the window it's already well under half.
    expect(bargeInCollapse(0.5)).toBeLessThan(0.5);
  });
});
