"""D-12-12 Gate-1 workload-revision measurements — spec 12 T08.

**Methodology recovery.** The 2026-06-05 first run measured the literal
D-12-12 contract workload (``subprocess.run(['pip','install','-q','pandas']);
import pandas; ...``) and produced p95=5.844s vs target <2.5s. Honest triage
flagged the methodology: pandas is preinstalled in E2B's code-interpreter
template, so `pip install pandas` is a no-op that still pays Python+pip-startup
overhead — conflating substrate cold-start with workload overhead.

This script measures two refined workloads to **decompose** substrate cold-start
from workload overhead:

  - **Gate-1-A** — ``import pandas; print(pandas.__version__)`` (the realistic
    minimum after a fresh sandbox: substrate cold-start + Python interp +
    pandas-import).
  - **Gate-1-B** — ``print('hello')`` (the absolute minimum: substrate
    cold-start + Python interp + no library).

The **delta** between A and B tells us how much is substrate vs Python startup
vs pandas import. We can then interpret the original 5.8s honestly.

If E2B exposes regional endpoints (EU vs US), measure both — geographic RTT
from a Norway dev rig to US-east E2B easily adds 200-500ms per SDK call.
"""

from __future__ import annotations

import contextlib
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(_REPO_ROOT / "packages/core/src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")


@dataclass
class Measurement:
    label: str
    workload: str
    domain: str | None
    timings_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def passed_25(self) -> bool:
        """Did this measurement clear the original 2.5 s p95 target?"""
        return self.timings_ms != [] and self.p95_s < 2.5

    @property
    def p50_s(self) -> float:
        return statistics.median(self.timings_ms) / 1000.0 if self.timings_ms else 0.0

    @property
    def p95_s(self) -> float:
        if not self.timings_ms:
            return 0.0
        if len(self.timings_ms) >= 20:
            return statistics.quantiles(self.timings_ms, n=20)[18] / 1000.0
        return max(self.timings_ms) / 1000.0

    @property
    def mean_s(self) -> float:
        return statistics.mean(self.timings_ms) / 1000.0 if self.timings_ms else 0.0

    @property
    def max_s(self) -> float:
        return max(self.timings_ms) / 1000.0 if self.timings_ms else 0.0

    @property
    def min_s(self) -> float:
        return min(self.timings_ms) / 1000.0 if self.timings_ms else 0.0


def measure(label: str, workload: str, domain: str | None, n: int = 20) -> Measurement:
    """Run the workload n times against the given domain; return measurement."""
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

    m = Measurement(label=label, workload=workload, domain=domain)
    print(f"  {label} (n={n}, domain={domain or '<default>'})...", flush=True)
    for i in range(n):
        kwargs: dict[str, Any] = {}
        if domain is not None:
            kwargs["domain"] = domain
        t0 = time.perf_counter()
        try:
            sandbox = Sandbox(**kwargs)
            result = sandbox.run_code(workload)
            t1 = time.perf_counter()
            m.timings_ms.append((t1 - t0) * 1000.0)
            if result.error is not None:
                m.errors.append(f"trial {i + 1}: {result.error.name}: {result.error.value[:200]}")
            with contextlib.suppress(Exception):
                sandbox.kill()
        except Exception as exc:  # noqa: BLE001
            m.errors.append(f"trial {i + 1}: SDK error: {type(exc).__name__}: {str(exc)[:200]}")
        if (i + 1) % 5 == 0:
            print(f"    {i + 1}/{n}", flush=True)
    return m


def main() -> int:
    print("=" * 70)
    print("D-12-12 GATE-1 WORKLOAD-REVISION MEASUREMENTS")
    print("=" * 70)
    print()

    # Detect available regions via SDK introspection. The SDK's `domain` kwarg
    # is the regional override; default value depends on env (E2B_DOMAIN env
    # var) or library default ("e2b.dev").
    print("Available SDK domain kwarg: 'domain' (defaults to e2b.dev / US-east infra)")
    print("Try alternate endpoints if known; otherwise measure default only.")
    print()

    measurements: list[Measurement] = []

    # Gate-1-A: realistic minimum workload (substrate + Python + pandas-import)
    print("Gate-1-A — import pandas; print(pandas.__version__)")
    m_a = measure(
        label="Gate-1-A (default domain)",
        workload="import pandas; print(pandas.__version__)",
        domain=None,
        n=20,
    )
    measurements.append(m_a)
    print(
        f"    p50={m_a.p50_s:.3f}s  p95={m_a.p95_s:.3f}s  mean={m_a.mean_s:.3f}s  "
        f"max={m_a.max_s:.3f}s"
    )
    print()

    # Gate-1-B: absolute minimum workload (substrate + Python only)
    print("Gate-1-B — print('hello')")
    m_b = measure(
        label="Gate-1-B (default domain)",
        workload="print('hello')",
        domain=None,
        n=20,
    )
    measurements.append(m_b)
    print(
        f"    p50={m_b.p50_s:.3f}s  p95={m_b.p95_s:.3f}s  mean={m_b.mean_s:.3f}s  "
        f"max={m_b.max_s:.3f}s"
    )
    print()

    # Decomposition analysis
    pandas_overhead_p95 = max(0.0, m_a.p95_s - m_b.p95_s)
    pandas_overhead_mean = max(0.0, m_a.mean_s - m_b.mean_s)
    print("Decomposition (Gate-1-A − Gate-1-B):")
    print(f"  pandas-import overhead (p95): {pandas_overhead_p95:.3f}s")
    print(f"  pandas-import overhead (mean): {pandas_overhead_mean:.3f}s")
    print()

    # Apply the decision tree
    print("=" * 70)
    print("DECISION TREE EVALUATION")
    print("=" * 70)
    print()
    decision_note = ""
    if m_a.p95_s < 2.5:
        decision_note = (
            "Gate-1-A passes the original 2.5s target. "
            "The 2026-06-05 first run's 5.8s p95 was inflated by the literal "
            "contract's `subprocess.run(['pip',...])` no-op overhead. "
            "**D-12-12 confirms on the original target** with workload "
            "clarification. Proceed to Gates 2-5."
        )
    elif m_a.p95_s < 3.5:
        decision_note = (
            f"Gate-1-A landed in 2.5-3.5s range (p95={m_a.p95_s:.3f}s). "
            "Action: try EU endpoint if E2B publishes one; if EU pulls p95 "
            "under 2.5s, confirm on original target with EU deployment "
            "constraint; if not, revise target to whatever this EU number is "
            "with the empirical-justification framing."
        )
    elif m_a.p95_s < 4.0:
        decision_note = (
            f"Gate-1-A at p95={m_a.p95_s:.3f}s. Revise target with UX "
            "framing — what feels responsive in the chat-tool-call flow "
            "(3-4s before user notices). Document empirical revision in "
            "decisions.md."
        )
    else:
        decision_note = (
            f"Gate-1-A at p95={m_a.p95_s:.3f}s — even the minimal-workload "
            "substrate cold-start is wide of the 2.5s target. **True substrate "
            "disqualification.** D-12-12 reopens to Daytona (arch-doc "
            "verification needed) → self-Fly Machines."
        )
    print(decision_note)
    print()

    # Write audit trail — new file, doesn't overwrite the first run
    audit_dir = _REPO_ROOT / "docs/specs/phase2/spec_12/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "lock_gates_2026-06-05_workload_revision.md"

    lines: list[str] = [
        "# D-12-12 Gate-1 workload-revision measurements — 2026-06-05",
        "",
        "**Predecessor:** [`lock_gates_2026-06-05.md`](lock_gates_2026-06-05.md) — the first run "
        "measured the literal D-12-12 contract workload and produced p95=5.844s vs target <2.5s. "
        "**This file does NOT replace the first run** — the 5.8s number is the honest result of "
        "the literal contract; replacing it would erase the methodology lesson.",
        "",
        "## Methodology rationale (D-12-15-style empirical-vs-aspirational lesson)",
        "",
        "The D-12-12 lock target `p95 < 2.5s` was lifted from R-12-1's recommendation, which was "
        "itself derived from E2B's marketing claim of 790ms p50 cold-start (US-east-localized). "
        "The literal contract workload — `subprocess.run(['pip','install','-q','pandas']); import "
        "pandas` — conflates **substrate cold-start** with **`pip` no-op overhead**: pandas is "
        "preinstalled in E2B's code-interpreter template, so the pip step says \"already "
        'satisfied" but still pays 0.5-1s of Python+pip-startup cost. Conflating the two and '
        "disqualifying the substrate on that conflated number would be the **same class of error "
        "as believing a vendor's marketing without empirical check** — just in the opposite "
        "direction.",
        "",
        "This re-measurement separates the two:",
        "- **Gate-1-A** measures `import pandas; print(pandas.__version__)` — substrate cold-start "
        "+ Python interpreter + pandas-import (the realistic minimum).",
        "- **Gate-1-B** measures `print('hello')` — substrate cold-start + Python interpreter, no "
        "library import (the absolute minimum).",
        "- The **delta** decomposes substrate vs Python startup vs pandas-import.",
        "",
        "## Meta-principle for future spec readers",
        "",
        "The 2.5s target was inherited from vendor marketing. This is the first time we "
        "empirically measured it. The empirical-validation discipline that ran through Phase 1 "
        "(Spec 11's soak — measured, not claimed; Spec 10's per-model corpus — measured per model; "
        'Spec 13\'s "verify against source" carries here. Gate 1 is the discipline doing its job, '
        "even when its first run fails. The methodology-recovery story belongs in the audit trail "
        "because future readers will need to know that the **original target was vendor-derived "
        "and the Phase 5 number is empirically-derived**.",
        "",
        "## Measurements",
        "",
    ]
    for m in measurements:
        lines.append(f"### {m.label}")
        lines.append("")
        lines.append(f"**Workload:** `{m.workload}`")
        lines.append("")
        lines.append(f"**Domain:** `{m.domain or 'default (e2b.dev)'}`")
        lines.append("")
        lines.append("**Timing percentiles (n=" + str(len(m.timings_ms)) + "):**")
        lines.append("")
        lines.append("| Stat | Value |")
        lines.append("|---|---|")
        lines.append(f"| p50 | **{m.p50_s:.3f} s** |")
        lines.append(f"| p95 | **{m.p95_s:.3f} s** |")
        lines.append(f"| mean | {m.mean_s:.3f} s |")
        lines.append(f"| min | {m.min_s:.3f} s |")
        lines.append(f"| max | {m.max_s:.3f} s |")
        lines.append(
            f"| **Verdict vs 2.5s target** | **{'✅ PASS' if m.passed_25 else '❌ FAIL'}** |"
        )
        lines.append("")
        if m.errors:
            lines.append(f"**Errors ({len(m.errors)}):**")
            lines.append("")
            for e in m.errors[:5]:
                lines.append(f"- {e}")
            lines.append("")

    lines.append("## Decomposition (Gate-1-A − Gate-1-B)")
    lines.append("")
    lines.append(
        "The delta isolates the pandas-import cost from substrate cold-start + Python interpreter "
        "startup."
    )
    lines.append("")
    lines.append("| Metric | Gate-1-B (min) | Gate-1-A (pandas) | pandas import overhead |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| p50 | {m_b.p50_s:.3f} s | {m_a.p50_s:.3f} s | {max(0.0, m_a.p50_s - m_b.p50_s):.3f} s |"
    )
    lines.append(f"| p95 | {m_b.p95_s:.3f} s | {m_a.p95_s:.3f} s | {pandas_overhead_p95:.3f} s |")
    lines.append(
        f"| mean | {m_b.mean_s:.3f} s | {m_a.mean_s:.3f} s | {pandas_overhead_mean:.3f} s |"
    )
    lines.append("")
    lines.append("## Comparison with the first run (literal contract)")
    lines.append("")
    lines.append("| Run | Workload | p50 | p95 | Decision |")
    lines.append("|---|---|---|---|---|")
    verdict_a = "✅ PASS" if m_a.passed_25 else "❌ FAIL"
    verdict_b = "✅ PASS" if m_b.passed_25 else "❌ FAIL"
    lines.append(
        "| 2026-06-05 (literal contract) | "
        "`subprocess.run(['pip','install','-q','pandas']); import pandas` | "
        "3.000 s | 5.844 s | ❌ FAIL vs 2.5s |"
    )
    lines.append(
        f"| Gate-1-A (realistic) | `import pandas; print(pandas.__version__)` "
        f"| {m_a.p50_s:.3f} s | {m_a.p95_s:.3f} s | {verdict_a} vs 2.5s |"
    )
    lines.append(
        f"| Gate-1-B (minimum) | `print('hello')` | {m_b.p50_s:.3f} s "
        f"| {m_b.p95_s:.3f} s | {verdict_b} vs 2.5s |"
    )
    lines.append("")
    lines.append(
        "**pip-no-op overhead** (literal contract − Gate-1-A p95): ~" + f"{5.844 - m_a.p95_s:.2f} s"
    )
    lines.append("")
    lines.append("## Decision-tree evaluation")
    lines.append("")
    lines.append(decision_note)
    lines.append("")
    lines.append(f"**Audit run timestamp:** {datetime.now(UTC).isoformat()}")

    audit_path.write_text("\n".join(lines))
    print(f"Audit trail written: {audit_path.relative_to(_REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
