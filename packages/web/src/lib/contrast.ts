/**
 * Spec F1 T14 — WCAG contrast + colour-vision-deficiency math.
 *
 * Pure functions, no dependencies. Used by contrast.test.ts (the Phase-5 T14
 * verification pass) and documented in DESIGN.md (T13).
 *
 * The pipeline:
 *   OKLCH → OKLab → linear LMS → linear sRGB → sRGB (with gamma) → WCAG luma
 *
 * For CVD sim we use the Brettel-Mollon-Viénot model (LMS-space confusion
 * line) — the same model the modern colour-blindness simulator libraries
 * implement. Three confusion types: protanopia (L-cone loss), deuteranopia
 * (M-cone loss), tritanopia (S-cone loss).
 *
 * Sources cross-referenced:
 *   - OKLab spec: https://bottosson.github.io/posts/oklab/
 *   - WCAG 2.2 contrast: https://www.w3.org/TR/WCAG22/#contrast-minimum
 *   - Brettel-Viénot CVD: Viénot et al. (1999); reference matrices from the
 *     `color-blind` npm package (MIT) and the W3C colour-vision deficiency
 *     compendium.
 */

/* -------------------------------------------------------------------------- */
/* OKLCH → sRGB (forward pipeline)                                            */
/* -------------------------------------------------------------------------- */

/** Linear sRGB channel `(r, g, b)`, components in [0, 1] (may go out of range
 *  before clamping when the OKLCH input is outside the sRGB gamut). */
export interface LinearRGB {
  r: number;
  g: number;
  b: number;
}

/** Gamma-encoded sRGB channel `(r, g, b)`, components in [0, 1]. */
export interface SRGB {
  r: number;
  g: number;
  b: number;
}

/** Parse an `oklch(L C H)` or `oklch(L C H / alpha)` CSS string into
 *  components. Returns L in [0,1], C and H as provided. */
export function parseOklch(s: string): { l: number; c: number; h: number } {
  const m = s
    .trim()
    .match(
      /^oklch\(\s*([\d.]+%?)\s+([\d.]+%?)\s+([\d.]+)(?:\s*\/\s*[^)]+)?\)$/i,
    );
  if (!m) throw new Error(`Invalid OKLCH string: ${s}`);
  const l = m[1].endsWith("%")
    ? Number.parseFloat(m[1]) / 100
    : Number.parseFloat(m[1]);
  const c = m[2].endsWith("%")
    ? Number.parseFloat(m[2]) * 0.4
    : Number.parseFloat(m[2]);
  return { l, c, h: Number.parseFloat(m[3]) };
}

/** OKLab → linear sRGB (Ottosson, 2020). Matrices baked in for speed. */
function oklabToLinearRgb(L: number, a: number, b: number): LinearRGB {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return {
    r: 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    g: -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    b: -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  };
}

/** Apply the sRGB gamma curve (linear → sRGB encoded). Output clamped to
 *  [0, 1] — OKLCH triples outside the sRGB gamut are clipped at the boundary,
 *  matching what a browser renders. */
function linearToSrgb(rgb: LinearRGB): SRGB {
  const f = (x: number): number => {
    const v = Math.max(0, Math.min(1, x));
    return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
  };
  return { r: f(rgb.r), g: f(rgb.g), b: f(rgb.b) };
}

/** OKLCH (CSS) → sRGB. Used by every other function in this module. */
export function oklchToSrgb(oklch: { l: number; c: number; h: number }): SRGB {
  const aRad = (oklch.h * Math.PI) / 180;
  const a = oklch.c * Math.cos(aRad);
  const b = oklch.c * Math.sin(aRad);
  return linearToSrgb(oklabToLinearRgb(oklch.l, a, b));
}

/* -------------------------------------------------------------------------- */
/* WCAG contrast ratio                                                        */
/* -------------------------------------------------------------------------- */

/** sRGB → relative luminance (WCAG 2.x definition). */
function relativeLuminance(s: SRGB): number {
  const f = (c: number): number =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  return 0.2126 * f(s.r) + 0.7152 * f(s.g) + 0.0722 * f(s.b);
}

/** WCAG contrast ratio between two OKLCH-string colours. */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(oklchToSrgb(parseOklch(a)));
  const lb = relativeLuminance(oklchToSrgb(parseOklch(b)));
  const [lo, hi] = la < lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** WCAG AA thresholds (the audit point). Body text = 4.5; UI / large text =
 *  3.0. Use the `large` threshold for accent-grade per-persona identity
 *  colours against the surface — they're decorative markers, not text. */
export const WCAG_AA = Object.freeze({
  normalText: 4.5,
  largeText: 3.0,
  uiNonText: 3.0,
});

/* -------------------------------------------------------------------------- */
/* CVD simulation (Brettel-Viénot LMS-space confusion lines)                  */
/* -------------------------------------------------------------------------- */

export type CVDType = "protanopia" | "deuteranopia" | "tritanopia";

/** Brettel-Viénot transform matrices for each CVD type, in sRGB space.
 *  Values converted from Viénot et al. (1999) Table 3, baked-in. */
const CVD_MATRICES: Record<CVDType, number[]> = {
  protanopia: [
    0.152286, 1.052583, -0.204868, 0.114503, 0.786281, 0.099216, -0.003882,
    -0.048116, 1.051998,
  ],
  deuteranopia: [
    0.367322, 0.860646, -0.227968, 0.280085, 0.672501, 0.047413, -0.01182,
    0.04294, 0.968881,
  ],
  tritanopia: [
    1.255528, -0.076749, -0.178779, -0.078411, 0.930809, 0.147602, 0.004733,
    0.691367, 0.3039,
  ],
};

/** Simulate how an sRGB colour appears under a given CVD type. */
export function simulateCVD(s: SRGB, type: CVDType): SRGB {
  const m = CVD_MATRICES[type];
  const r = m[0] * s.r + m[1] * s.g + m[2] * s.b;
  const g = m[3] * s.r + m[4] * s.g + m[5] * s.b;
  const b = m[6] * s.r + m[7] * s.g + m[8] * s.b;
  // Clamp; sim outputs can fall slightly outside [0,1].
  return {
    r: Math.max(0, Math.min(1, r)),
    g: Math.max(0, Math.min(1, g)),
    b: Math.max(0, Math.min(1, b)),
  };
}

/** Euclidean distance in sRGB between two SRGB colours. Used to assert
 *  pairwise distinguishability of the identity palette under CVD sim. Not a
 *  perceptual metric — but adequate as a regression guard at the size of the
 *  palette (12 entries). */
export function srgbDistance(a: SRGB, b: SRGB): number {
  const dr = a.r - b.r;
  const dg = a.g - b.g;
  const db = a.b - b.b;
  return Math.sqrt(dr * dr + dg * dg + db * db);
}
