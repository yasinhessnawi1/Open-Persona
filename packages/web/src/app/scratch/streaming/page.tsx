"use client";

/**
 * X-F2-1 streaming-renderer mechanism spike (Phase 3 measurement harness).
 *
 * Per the F2 kickoff §Concern 3 + the user's Phase 3 sign-off: D-F2-5 is
 * measured, not assumed. This is the testbed.
 *
 * Three candidate mechanisms (per exploration.md):
 *   A — Append-on-event with debounced React render (rAF-coalesced).
 *   B — useTransition + rAF-coalesced append (low-priority render lane).
 *   C — Mutable text-node updated outside React (zero reconciliation).
 *
 * Replay fixture: synthesised DeepSeek-realistic cadence (~50 tokens/sec,
 * ~3 chars/token, jittered 16-30ms inter-chunk). Includes four-rounds
 * tool-call markers so the interleave handling is exercised.
 *
 * Quantitative measurements (this page exposes them via DOM data-attributes
 * + window.__streamMetrics so a Playwright probe / DevTools console can
 * read them):
 *   - FPS (rAF-counted, refreshed every 1s)
 *   - Total chunks delivered
 *   - Total render commits (mechanism A/B only — C bypasses React)
 *   - Adjacent-input latency: time between keypress and next rAF frame,
 *     a proxy for "is the input lag perceptible while streaming."
 *
 * Qualitative: visual smoothness; the operator types into the adjacent
 * input during streaming to feel concurrent-interaction responsiveness.
 *
 * Caret colour is intentionally vermilion (--primary, the D-F2-12 lean)
 * but the mechanism choice is colour-agnostic (Phase 3 refinement 3).
 *
 * Dev-only: NODE_ENV check throws in production; metadata flags no-index.
 * Harness stays in-tree for Phase 6 criterion-#11 review re-runs
 * (Phase 3 refinement 1).
 */

import {
  type ChangeEvent,
  type CSSProperties,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
} from "react";

// ---------------------------------------------------------------------------
// Production guard (Phase 3 refinement 1: harness stays in-tree but dev-only)

if (process.env.NODE_ENV === "production") {
  throw new Error(
    "/scratch/* routes are development-only. This page must not render in production.",
  );
}

// ---------------------------------------------------------------------------
// Fixture: a realistic Astrid response with embedded tool-call interleave
// markers ([TOOL: web_search] ... [/TOOL]). Length ~1500 chars — enough to
// sustain a measurable stream for ~15-20 seconds at DeepSeek's cadence.

const FIXTURE_TEXT = `Good morning. You're asking about a landlord refusing to fix mould in a Norwegian rental — let me think this through carefully.

[TOOL: web_search] husleieloven § 5-1 mould responsibility tenant Norway [/TOOL]

The husleieloven (the Norwegian Tenancy Act) places clear responsibility on the landlord for maintaining the dwelling in tenantable condition. Section 5-1 establishes that the landlord must hand over the property in a condition that meets the standard expected for residential use. Mould is a serious habitability issue and falls squarely under this provision.

[TOOL: web_search] husleieloven § 5-7 tenant right to demand repair [/TOOL]

Section 5-7 gives you, as the tenant, the right to demand that the landlord remedy defects within a reasonable time. If the landlord fails to do so, you have several options:

First, you can withhold a proportionate share of the rent until the issue is fixed. Second, you can have the work done yourself and deduct the reasonable cost from rent. Third, you can take the dispute to Husleietvistutvalget — the Tenancy Dispute Resolution Committee — which is typically faster and cheaper than court.

A few important practical notes. Document everything: photograph the mould, save written communications with the landlord, get a doctor's note if you're experiencing health symptoms. The documentation is what makes the case in dispute resolution.

I should note that I cannot represent you in this matter. If the landlord refuses to engage, or if the situation escalates, you should consult a qualified lawyer — particularly if there are health consequences or you're considering terminating the tenancy.

Would you like me to help draft a written complaint to the landlord that cites the relevant statutory provisions?`;

interface ReplayChunk {
  text: string;
  delayMs: number;
}

function buildReplay(text: string, seed: number): ReplayChunk[] {
  // Deterministic LCG so each mechanism replays the EXACT same cadence —
  // comparisons across A/B/C are apples-to-apples.
  let s = seed;
  const rand = () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
  const chunks: ReplayChunk[] = [];
  for (let i = 0; i < text.length; i += 3) {
    chunks.push({
      text: text.slice(i, i + 3),
      delayMs: 16 + rand() * 14, // 16-30ms ⇒ ~33-60 chunks/sec ⇒ ~100-180 chars/sec
    });
  }
  return chunks;
}

// ---------------------------------------------------------------------------
// FPS counter (rAF-based; reports updates every 1000ms).

function useFps(): number {
  const [fps, setFps] = useState(60);
  useEffect(() => {
    let frames = 0;
    let lastReport = performance.now();
    let raf = 0;
    const loop = (now: number) => {
      frames++;
      const dt = now - lastReport;
      if (dt >= 1000) {
        setFps(Math.round((frames * 1000) / dt));
        frames = 0;
        lastReport = now;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);
  return fps;
}

// ---------------------------------------------------------------------------
// Adjacent-input latency probe: timestamp the keypress; resolve on the next
// rAF frame; record the delta. Higher delta = more perceived input lag
// while streaming.

interface LatencyStats {
  count: number;
  min: number;
  max: number;
  avg: number;
  p95: number;
  samples: number[];
}

function emptyStats(): LatencyStats {
  return { count: 0, min: 0, max: 0, avg: 0, p95: 0, samples: [] };
}

function useInputLatencyProbe() {
  const [stats, setStats] = useState<LatencyStats>(emptyStats());
  const samples = useRef<number[]>([]);

  const onInput = useCallback((_e: ChangeEvent<HTMLInputElement>) => {
    const t0 = performance.now();
    requestAnimationFrame(() => {
      const dt = performance.now() - t0;
      samples.current.push(dt);
      const s = [...samples.current];
      s.sort((a, b) => a - b);
      const sum = s.reduce((a, b) => a + b, 0);
      const p95idx = Math.min(s.length - 1, Math.floor(s.length * 0.95));
      setStats({
        count: s.length,
        min: s[0],
        max: s[s.length - 1],
        avg: sum / s.length,
        p95: s[p95idx],
        samples: samples.current.slice(-200), // keep last 200 for export
      });
    });
  }, []);

  const reset = useCallback(() => {
    samples.current = [];
    setStats(emptyStats());
  }, []);

  return { stats, onInput, reset };
}

// ---------------------------------------------------------------------------
// Replay driver: shared across mechanisms. Calls onChunk(text) at the
// fixture's recorded cadence.

function useReplay(
  chunks: ReplayChunk[],
  onChunk: (s: string) => void,
  start: boolean,
  onComplete: () => void,
) {
  useEffect(() => {
    if (!start) return;
    let i = 0;
    let timeout: ReturnType<typeof setTimeout> | null = null;
    const tick = () => {
      if (i >= chunks.length) {
        onComplete();
        return;
      }
      const c = chunks[i++];
      onChunk(c.text);
      timeout = setTimeout(tick, c.delayMs);
    };
    timeout = setTimeout(tick, 0);
    return () => {
      if (timeout) clearTimeout(timeout);
    };
  }, [chunks, onChunk, start, onComplete]);
}

// ---------------------------------------------------------------------------
// Caret — vermilion lean (D-F2-12). Pulse is decorative; reduced-motion
// silences it via the F1 T15 path. Aria-hidden — the live region wrapping
// the text announces; the caret is decorative-only.

function Caret() {
  return (
    <span
      aria-hidden="true"
      className="ml-0.5 inline-block h-4 w-[3px] translate-y-0.5 animate-pulse rounded-full bg-primary"
    />
  );
}

// ---------------------------------------------------------------------------
// Mechanism A — Debounced + rAF-coalesced React render.

function MechanismA({
  chunks,
  start,
  onComplete,
  onCommit,
}: {
  chunks: ReplayChunk[];
  start: boolean;
  onComplete: () => void;
  onCommit: () => void;
}) {
  const [text, setText] = useState("");
  const buffer = useRef<string[]>([]);
  const rafId = useRef<number | null>(null);

  const flush = useCallback(() => {
    if (buffer.current.length === 0) {
      rafId.current = null;
      return;
    }
    const toAppend = buffer.current.join("");
    buffer.current = [];
    rafId.current = null;
    onCommit();
    setText((prev) => prev + toAppend);
  }, [onCommit]);

  const onChunk = useCallback(
    (s: string) => {
      buffer.current.push(s);
      if (rafId.current === null) {
        rafId.current = requestAnimationFrame(flush);
      }
    },
    [flush],
  );

  useReplay(chunks, onChunk, start, onComplete);

  return (
    <output className="type-body block whitespace-pre-wrap" aria-live="polite">
      {text}
      <Caret />
    </output>
  );
}

// ---------------------------------------------------------------------------
// Mechanism B — useTransition + rAF-coalesced append.

function MechanismB({
  chunks,
  start,
  onComplete,
  onCommit,
}: {
  chunks: ReplayChunk[];
  start: boolean;
  onComplete: () => void;
  onCommit: () => void;
}) {
  const [text, setText] = useState("");
  const [, startTransition] = useTransition();
  const buffer = useRef<string[]>([]);
  const rafId = useRef<number | null>(null);

  const flush = useCallback(() => {
    if (buffer.current.length === 0) {
      rafId.current = null;
      return;
    }
    const toAppend = buffer.current.join("");
    buffer.current = [];
    rafId.current = null;
    onCommit();
    startTransition(() => {
      setText((prev) => prev + toAppend);
    });
  }, [onCommit]);

  const onChunk = useCallback(
    (s: string) => {
      buffer.current.push(s);
      if (rafId.current === null) {
        rafId.current = requestAnimationFrame(flush);
      }
    },
    [flush],
  );

  useReplay(chunks, onChunk, start, onComplete);

  return (
    <output className="type-body block whitespace-pre-wrap" aria-live="polite">
      {text}
      <Caret />
    </output>
  );
}

// ---------------------------------------------------------------------------
// Mechanism C — Mutable text-node, outside React. Zero reconciliation.

function MechanismC({
  chunks,
  start,
  onComplete,
  onCommit,
}: {
  chunks: ReplayChunk[];
  start: boolean;
  onComplete: () => void;
  onCommit: () => void;
}) {
  const textRef = useRef<HTMLSpanElement>(null);

  const onChunk = useCallback(
    (s: string) => {
      if (textRef.current) {
        textRef.current.textContent = (textRef.current.textContent ?? "") + s;
        onCommit();
      }
    },
    [onCommit],
  );

  // Reset textContent when stream restarts (on `start` flip-to-true).
  useEffect(() => {
    if (start && textRef.current) {
      textRef.current.textContent = "";
    }
  }, [start]);

  useReplay(chunks, onChunk, start, onComplete);

  return (
    <output className="type-body block whitespace-pre-wrap" aria-live="polite">
      <span ref={textRef} />
      <Caret />
    </output>
  );
}

// ---------------------------------------------------------------------------
// Page — the operator UI + the measurement panel.

type Mechanism = "A" | "B" | "C";

interface RunMetrics {
  mechanism: Mechanism;
  startedAt: number;
  completedAt?: number;
  chunksDelivered: number;
  commitsObserved: number; // rAF flushes (A/B) or direct writes (C)
  fpsSamples: number[];
}

declare global {
  interface Window {
    __streamMetrics?: {
      runs: RunMetrics[];
      current?: RunMetrics;
      inputLatency: LatencyStats;
    };
  }
}

export default function ScratchStreamingPage() {
  const [mech, setMech] = useState<Mechanism>("B");
  const [start, setStart] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [adjacentInput, setAdjacentInput] = useState("");
  const [runs, setRuns] = useState<RunMetrics[]>([]);

  const fps = useFps();
  const {
    stats: inputLatency,
    onInput,
    reset: resetInput,
  } = useInputLatencyProbe();

  // Same seed across mechanisms → identical cadence; comparable.
  const chunks = useMemo(() => buildReplay(FIXTURE_TEXT, 42), []);

  const currentRun = useRef<RunMetrics | null>(null);

  // FPS rolling sample into the current run.
  useEffect(() => {
    if (currentRun.current && start && !completed) {
      currentRun.current.fpsSamples.push(fps);
    }
  }, [fps, start, completed]);

  const begin = useCallback(() => {
    const run: RunMetrics = {
      mechanism: mech,
      startedAt: performance.now(),
      chunksDelivered: 0,
      commitsObserved: 0,
      fpsSamples: [],
    };
    currentRun.current = run;
    if (typeof window !== "undefined") {
      window.__streamMetrics = {
        runs: window.__streamMetrics?.runs ?? [],
        current: run,
        inputLatency,
      };
    }
    // Reset input-latency stats so each run's adjacent-typing samples are
    // independent (cross-mechanism comparison is apples-to-apples).
    resetInput();
    setCompleted(false);
    setStart(false); // reset
    // Tiny defer so the mechanism component re-mounts on `key` change.
    setTimeout(() => setStart(true), 16);
  }, [mech, inputLatency, resetInput]);

  const onComplete = useCallback(() => {
    if (!currentRun.current) return;
    currentRun.current.completedAt = performance.now();
    const completed_ = { ...currentRun.current };
    setRuns((prev) => [...prev, completed_]);
    if (typeof window !== "undefined" && window.__streamMetrics) {
      window.__streamMetrics.runs.push(completed_);
      window.__streamMetrics.current = undefined;
    }
    // Stop the replay BEFORE the runs[] update flips the mechanism key —
    // otherwise the remount lands with start=true and the replay restarts
    // (first-measurement bug, caught 2026-06-05). Order matters: setStart
    // first so the next render commits with start=false.
    setStart(false);
    setCompleted(true);
    currentRun.current = null;
  }, []);

  const onCommit = useCallback(() => {
    if (currentRun.current) {
      currentRun.current.commitsObserved++;
      currentRun.current.chunksDelivered++;
    }
  }, []);

  // Live-export the metrics on window so a Playwright probe can read them
  // without DOM scraping.
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.__streamMetrics = {
      runs,
      current: currentRun.current ?? undefined,
      inputLatency,
    };
  }, [runs, inputLatency]);

  const metricsPanelStyle: CSSProperties = {
    fontFamily: "var(--font-mono)",
    fontSize: "var(--text-caption-size)",
  };

  return (
    <main className="mx-auto max-w-3xl space-y-6 p-8">
      <header className="space-y-2">
        <h1 className="type-display">X-F2-1 streaming-renderer spike</h1>
        <p className="type-ui text-muted-foreground">
          D-F2-5 measurement testbed. Three candidate mechanisms; identical
          fixture (deterministic LCG seed = 42); same caret colour (vermilion
          lean for D-F2-12 — the choice here is colour-agnostic). Adjacent input
          below measures concurrent-interaction responsiveness — type into it
          during the stream to feel the lag (or absence thereof).
        </p>
      </header>

      <section className="space-y-3 rounded-lg border bg-card p-4">
        <div className="flex items-center gap-3">
          <label className="type-ui">
            Mechanism:{" "}
            <select
              value={mech}
              onChange={(e) => {
                setMech(e.target.value as Mechanism);
                setStart(false);
                setCompleted(false);
              }}
              className="ml-2 rounded border border-input bg-background px-2 py-1"
              data-testid="mechanism-select"
            >
              <option value="A">A — Debounced + rAF-coalesced</option>
              <option value="B">B — useTransition + rAF-coalesced</option>
              <option value="C">C — Mutable text-node (no React)</option>
            </select>
          </label>
          <button
            type="button"
            onClick={begin}
            className="rounded bg-primary px-3 py-1 text-primary-foreground"
            data-testid="start-stream"
          >
            Start stream
          </button>
          <button
            type="button"
            onClick={() => {
              setStart(false);
              setCompleted(false);
              resetInput();
            }}
            className="rounded border border-border px-3 py-1"
            data-testid="reset-stream"
          >
            Reset
          </button>
        </div>

        <div
          className="grid grid-cols-3 gap-3 text-muted-foreground"
          style={metricsPanelStyle}
        >
          <div>
            FPS:{" "}
            <span className="text-foreground" data-testid="fps-current">
              {fps}
            </span>
          </div>
          <div>
            Chunks delivered:{" "}
            <span className="text-foreground" data-testid="chunks-delivered">
              {currentRun.current?.chunksDelivered ?? 0}
            </span>
          </div>
          <div>
            Render commits:{" "}
            <span className="text-foreground" data-testid="commits-observed">
              {currentRun.current?.commitsObserved ?? 0}
            </span>
          </div>
          <div>
            Input lag avg:{" "}
            <span className="text-foreground" data-testid="input-latency-avg">
              {inputLatency.avg.toFixed(1)}ms
            </span>
          </div>
          <div>
            Input lag p95:{" "}
            <span className="text-foreground" data-testid="input-latency-p95">
              {inputLatency.p95.toFixed(1)}ms
            </span>
          </div>
          <div>
            Input lag max:{" "}
            <span className="text-foreground" data-testid="input-latency-max">
              {inputLatency.max.toFixed(1)}ms
            </span>
          </div>
        </div>
      </section>

      <section
        className="min-h-[320px] rounded-lg border border-l-2 border-l-primary bg-card p-4"
        data-testid="stream-surface"
      >
        {/* `key` change forces unmount on mechanism switch — clean reset. */}
        {mech === "A" && (
          <MechanismA
            key={`A-${runs.length}-${start ? "go" : "idle"}`}
            chunks={chunks}
            start={start}
            onComplete={onComplete}
            onCommit={onCommit}
          />
        )}
        {mech === "B" && (
          <MechanismB
            key={`B-${runs.length}-${start ? "go" : "idle"}`}
            chunks={chunks}
            start={start}
            onComplete={onComplete}
            onCommit={onCommit}
          />
        )}
        {mech === "C" && (
          <MechanismC
            key={`C-${runs.length}-${start ? "go" : "idle"}`}
            chunks={chunks}
            start={start}
            onComplete={onComplete}
            onCommit={onCommit}
          />
        )}
      </section>

      <section className="space-y-2">
        <label className="type-ui block" htmlFor="adjacent-input">
          Adjacent input (type during streaming to measure
          concurrent-interaction responsiveness — the real &quot;smooth under
          user interaction&quot; goal):
        </label>
        <input
          id="adjacent-input"
          type="text"
          value={adjacentInput}
          onChange={(e) => {
            setAdjacentInput(e.target.value);
            onInput(e);
          }}
          placeholder="Try typing here while the stream runs"
          className="w-full rounded border border-input bg-background px-3 py-2"
          data-testid="adjacent-input"
        />
      </section>

      {runs.length > 0 && (
        <section className="space-y-2">
          <h2 className="type-heading">Completed runs</h2>
          <table
            className="w-full text-muted-foreground"
            style={metricsPanelStyle}
            data-testid="runs-table"
          >
            <thead>
              <tr className="text-left">
                <th className="py-1">#</th>
                <th>Mech</th>
                <th>Duration</th>
                <th>Chunks</th>
                <th>Commits</th>
                <th>FPS min</th>
                <th>FPS avg</th>
                <th>FPS p5</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r, i) => {
                const dur = (r.completedAt ?? r.startedAt) - r.startedAt;
                const fpsSorted = [...r.fpsSamples].sort((a, b) => a - b);
                const fpsAvg =
                  fpsSorted.length === 0
                    ? 0
                    : fpsSorted.reduce((a, b) => a + b, 0) / fpsSorted.length;
                const fpsMin = fpsSorted[0] ?? 0;
                const fpsP5 =
                  fpsSorted[Math.max(0, Math.floor(fpsSorted.length * 0.05))] ??
                  0;
                return (
                  <tr key={`${r.mechanism}-${i}`} data-testid={`run-row-${i}`}>
                    <td className="py-1">{i + 1}</td>
                    <td className="text-foreground">{r.mechanism}</td>
                    <td>{(dur / 1000).toFixed(2)}s</td>
                    <td>{r.chunksDelivered}</td>
                    <td>{r.commitsObserved}</td>
                    <td>{fpsMin}</td>
                    <td>{fpsAvg.toFixed(1)}</td>
                    <td>{fpsP5}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="type-caption text-muted-foreground">
            FPS p5 is the 5th-percentile (worst-case sustained) frame rate. A
            mechanism that holds 60 FPS at p5 is buttery-smooth; under ~45 FPS
            at p5 is perceptibly janky.
          </p>
        </section>
      )}
    </main>
  );
}
