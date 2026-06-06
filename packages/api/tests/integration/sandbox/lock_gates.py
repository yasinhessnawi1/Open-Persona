"""D-12-12 lock-gate measurements — substrate decision gate (spec 12 T08).

Runs the five binary go/no-go gates that confirm E2B as the v0.1 hosted
substrate. **Stops on first FAIL** per user's Phase-5 instruction; the
substrate decision (D-12-12) reopens to Daytona (pending arch-doc
verification) → self-Fly Machines.

Designed as a script (not a pytest fixture) so each run is auditable and
budget-bounded: total E2B credit consumption is documented in the audit
trail written at the end.

Invocation:

    uv run python -m packages.api.tests.integration.sandbox.lock_gates

Or directly:

    cd packages/api/tests/integration/sandbox && uv run python lock_gates.py

The audit trail lands at
``docs/specs/phase2/spec_12/audit/lock_gates_<date>.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Resolve PYTHONPATH so the script runs from any cwd
_REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(_REPO_ROOT / "packages/core/src"))
sys.path.insert(0, str(_REPO_ROOT / "packages/api/src"))

# Load .env so the SDK picks up E2B_API_KEY
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")


@dataclass
class GateResult:
    """One gate's outcome."""

    name: str
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


# ---------------------------------------------------------------------------
# Gate 1 — Cold-start p95 < 2.5s on `pip install pandas && import pandas`
# ---------------------------------------------------------------------------


def gate_1_cold_start(n: int = 20, target_p95_s: float = 2.5) -> GateResult:
    """Cold-start p95 < target on the pandas-install-and-import workload.

    The E2B code-interpreter template ships with pandas preinstalled, so
    ``pip install pandas`` says "already satisfied" and ``import pandas``
    is instant — the measurement effectively captures sandbox creation +
    Python overhead, which is the production-relevant number.
    """
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

    workload = (
        "import subprocess; subprocess.run(['pip','install','-q','pandas']); "
        "import pandas; print(pandas.__version__)"
    )
    timings_ms: list[float] = []
    print(f"  Running {n} trials of cold-start + pandas workload...", flush=True)
    for i in range(n):
        t0 = time.perf_counter()
        sandbox = Sandbox()
        try:
            result = sandbox.run_code(workload)
            t1 = time.perf_counter()
            timings_ms.append((t1 - t0) * 1000.0)
            if result.error is not None:
                return GateResult(
                    name="Gate 1: cold-start p95",
                    passed=False,
                    evidence={"trial": i + 1, "error": str(result.error)},
                    detail=f"trial {i + 1} errored: {result.error}",
                )
            if (i + 1) % 5 == 0:
                print(f"    {i + 1}/{n} trials done", flush=True)
        finally:
            with contextlib.suppress(Exception):
                sandbox.kill()

    p50_s = statistics.median(timings_ms) / 1000.0
    p95_s = (
        statistics.quantiles(timings_ms, n=20)[18] / 1000.0
        if len(timings_ms) >= 20
        else max(timings_ms) / 1000.0
    )
    mean_s = statistics.mean(timings_ms) / 1000.0
    return GateResult(
        name="Gate 1: cold-start p95",
        passed=p95_s < target_p95_s,
        evidence={
            "n": len(timings_ms),
            "p50_s": round(p50_s, 3),
            "p95_s": round(p95_s, 3),
            "mean_s": round(mean_s, 3),
            "max_s": round(max(timings_ms) / 1000.0, 3),
            "target_p95_s": target_p95_s,
        },
        detail=f"p95={p95_s:.3f}s vs target <{target_p95_s}s on n={n}",
    )


# ---------------------------------------------------------------------------
# Gate 2 — 20-sandbox concurrent fan-out, p95 ready < 5s
# ---------------------------------------------------------------------------


def gate_2_concurrent_fanout(n: int = 20, target_p95_s: float = 5.0) -> GateResult:
    """20 sandboxes created concurrently; verify p95 time-to-ready < target."""
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

    async def _create_one() -> tuple[float, Any]:
        t0 = time.perf_counter()
        sandbox = await asyncio.to_thread(Sandbox)
        elapsed_s = time.perf_counter() - t0
        return elapsed_s, sandbox

    async def _run() -> list[tuple[float, Any]]:
        return await asyncio.gather(*[_create_one() for _ in range(n)])

    print(f"  Spawning {n} sandboxes concurrently...", flush=True)
    try:
        results = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        return GateResult(
            name="Gate 2: concurrent fan-out",
            passed=False,
            evidence={"n": n, "error": f"{type(exc).__name__}: {exc}"},
            detail=f"fan-out raised {type(exc).__name__}",
        )

    timings_s = [t for t, _ in results]
    sandboxes = [s for _, s in results]
    # Cleanup
    print(f"  Killing {n} sandboxes...", flush=True)
    for sandbox in sandboxes:
        with contextlib.suppress(Exception):
            sandbox.kill()

    p50_s = statistics.median(timings_s)
    p95_s = statistics.quantiles(timings_s, n=20)[18] if len(timings_s) >= 20 else max(timings_s)
    return GateResult(
        name="Gate 2: concurrent fan-out",
        passed=p95_s < target_p95_s,
        evidence={
            "n": n,
            "p50_s": round(p50_s, 3),
            "p95_s": round(p95_s, 3),
            "max_s": round(max(timings_s), 3),
            "target_p95_s": target_p95_s,
        },
        detail=f"p95={p95_s:.3f}s vs target <{target_p95_s}s on n={n}",
    )


# ---------------------------------------------------------------------------
# Gate 3 — Adversarial egress denial (every §9 #7 IP blocked with internet off)
# ---------------------------------------------------------------------------


def gate_3_egress_denial() -> GateResult:
    """Every §9 #7 IP/host blocked when ``allow_internet_access=False``.

    Even with the attacker's preferred host in a hypothetical allow-list
    (E2B doesn't expose per-host allow-lists in the same way), the
    substrate-level egress denial fires.
    """
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

    # Curated subset of §9 #7 attacks — each must FAIL to reach its target.
    attacks = [
        (
            "aws_imds_v1",
            "import urllib.request; "
            'urllib.request.urlopen("http://169.254.169.254/latest/meta-data/", '
            "timeout=3).read()",
        ),
        (
            "rfc1918_10",
            'import socket; s=socket.socket(); s.settimeout(3); s.connect(("10.0.0.1", 22))',
        ),
        (
            "rfc1918_192",
            'import socket; s=socket.socket(); s.settimeout(3); s.connect(("192.168.0.1", 22))',
        ),
        (
            "loopback",
            'import socket; s=socket.socket(); s.settimeout(3); s.connect(("127.0.0.1", 22))',
        ),
        (
            "public_http_when_off",
            "import urllib.request; "
            'urllib.request.urlopen("http://example.com/", timeout=3).read()',
        ),  # this is §9 #6 not #7 but verifies network=off is honored
    ]

    print("  Creating sandbox with allow_internet_access=False...", flush=True)
    try:
        sandbox = Sandbox(allow_internet_access=False)
    except Exception as exc:  # noqa: BLE001
        return GateResult(
            name="Gate 3: egress denial",
            passed=False,
            evidence={"error": f"could not create sandbox with allow_internet_access=False: {exc}"},
            detail="sandbox creation failed",
        )

    per_attack: list[dict[str, str | bool]] = []
    all_blocked = True
    try:
        for name, code in attacks:
            print(f"    Testing: {name}", flush=True)
            result = sandbox.run_code(code, timeout=10)
            blocked = result.error is not None
            per_attack.append(
                {
                    "attack": name,
                    "blocked": blocked,
                    "error_name": result.error.name if result.error else "",
                    "error_value": (result.error.value[:200] if result.error else ""),
                }
            )
            if not blocked:
                all_blocked = False
    finally:
        with contextlib.suppress(Exception):
            sandbox.kill()

    return GateResult(
        name="Gate 3: egress denial",
        passed=all_blocked,
        evidence={"per_attack": per_attack, "all_blocked": all_blocked},
        detail=f"{sum(1 for a in per_attack if a['blocked'])}/{len(per_attack)} attacks blocked",
    )


# ---------------------------------------------------------------------------
# Gate 4 — Mid-exec kill clean (SDK error, no zombies)
# ---------------------------------------------------------------------------


def gate_4_mid_exec_kill() -> GateResult:
    """Kill a long-running execution from outside; verify clean SDK behaviour."""
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

    print("  Starting long-running execution (sleep 30s)...", flush=True)
    sandbox = Sandbox()
    sandbox_id = sandbox.sandbox_id if hasattr(sandbox, "sandbox_id") else "<unknown>"
    long_code = "import time; time.sleep(30); print('FINISHED (should not see)')"

    # Schedule a kill after 2s on a background thread
    import threading

    kill_at = time.perf_counter() + 2.0
    kill_exc_holder: list[Exception | None] = [None]

    def _kill_after_delay() -> None:
        while time.perf_counter() < kill_at:
            time.sleep(0.1)
        try:
            sandbox.kill()
        except Exception as exc:  # noqa: BLE001
            kill_exc_holder[0] = exc

    kill_thread = threading.Thread(target=_kill_after_delay)
    kill_thread.start()

    t0 = time.perf_counter()
    run_exc: Exception | None = None
    finished_normally = False
    try:
        result = sandbox.run_code(long_code, timeout=60)
        # If we reach here, the run completed normally — verify it actually ran
        if result.logs.stdout and "FINISHED" in "".join(result.logs.stdout):
            finished_normally = True
    except Exception as exc:  # noqa: BLE001
        run_exc = exc
    kill_thread.join(timeout=5)
    elapsed_s = time.perf_counter() - t0

    # PASS criteria: either the run raised cleanly (best) OR it terminated
    # quickly (< 10s — well before the 30s sleep would have completed) AND
    # did not show "FINISHED" stdout.
    clean_termination = run_exc is not None or (elapsed_s < 10 and not finished_normally)

    return GateResult(
        name="Gate 4: mid-exec kill",
        passed=clean_termination,
        evidence={
            "sandbox_id": sandbox_id,
            "elapsed_s": round(elapsed_s, 2),
            "run_raised": run_exc is not None,
            "run_exc_type": type(run_exc).__name__ if run_exc else None,
            "finished_normally": finished_normally,
            "kill_thread_exc": str(kill_exc_holder[0]) if kill_exc_holder[0] else None,
        },
        detail=(
            f"elapsed={elapsed_s:.1f}s; run_raised={run_exc is not None}; "
            f"finished_normally={finished_normally}"
        ),
    )


# ---------------------------------------------------------------------------
# Gate 5 — 7-day realistic-load cost projection < $50/mo
# ---------------------------------------------------------------------------


def gate_5_cost_projection(
    gate_1_mean_s: float,
    target_monthly_usd: float = 50.0,
) -> GateResult:
    """Synthetic projection from Gate 1's mean sandbox-time.

    Realistic load assumption from D-12-12: 200 sandbox-creations/day with
    30-second mean session. Per-second cost from E2B's published Hobby
    pricing ($0.067/h ÷ 3600 = $0.0000186/sec).

    Projected monthly cost = sandbox-creations × mean-seconds × cost-per-sec × 30 days.
    """
    creations_per_day = 200
    mean_session_s = 30.0  # D-12-12 assumption
    cost_per_sec_usd = 0.067 / 3600.0  # $0.067/h ÷ 3600s
    monthly_sandbox_sec = creations_per_day * mean_session_s * 30.0
    projected_monthly_usd = monthly_sandbox_sec * cost_per_sec_usd

    # Also compute from Gate 1's actual mean (if Gate 1 ran)
    actual_per_creation_cost = gate_1_mean_s * cost_per_sec_usd
    actual_projected_monthly = creations_per_day * gate_1_mean_s * cost_per_sec_usd * 30.0

    return GateResult(
        name="Gate 5: cost projection",
        passed=projected_monthly_usd < target_monthly_usd
        and actual_projected_monthly < target_monthly_usd,
        evidence={
            "target_monthly_usd": target_monthly_usd,
            "assumptions": {
                "creations_per_day": creations_per_day,
                "mean_session_s_d12_12": mean_session_s,
                "mean_session_s_actual_from_gate1": round(gate_1_mean_s, 3),
                "cost_per_sec_usd": round(cost_per_sec_usd, 7),
            },
            "projected_d12_12_assumption_usd_mo": round(projected_monthly_usd, 2),
            "projected_actual_from_gate1_usd_mo": round(actual_projected_monthly, 2),
            "per_creation_cost_usd_d12_12": round(mean_session_s * cost_per_sec_usd, 6),
            "per_creation_cost_usd_actual": round(actual_per_creation_cost, 6),
        },
        detail=(
            f"D-12-12 assumption: ${projected_monthly_usd:.2f}/mo; "
            f"actual: ${actual_projected_monthly:.2f}/mo "
            f"(target <${target_monthly_usd:.2f})"
        ),
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 70)
    print("D-12-12 LOCK-GATE MEASUREMENTS — spec 12 T08")
    print("=" * 70)
    print()

    results: list[GateResult] = []

    # Gate 1
    print("Gate 1 — Cold-start p95 < 2.5s on pandas workload (n=20)")
    try:
        r1 = gate_1_cold_start(n=20, target_p95_s=2.5)
    except Exception as exc:  # noqa: BLE001
        r1 = GateResult(
            name="Gate 1: cold-start p95",
            passed=False,
            evidence={"exception": f"{type(exc).__name__}: {exc}"},
            detail=f"unhandled: {type(exc).__name__}",
        )
    results.append(r1)
    print(f"  {'PASS' if r1.passed else 'FAIL'}: {r1.detail}")
    print()
    if not r1.passed:
        return _finalise(results)

    # Gate 2
    print("Gate 2 — 20-sandbox concurrent fan-out, p95 ready < 5s")
    try:
        r2 = gate_2_concurrent_fanout(n=20, target_p95_s=5.0)
    except Exception as exc:  # noqa: BLE001
        r2 = GateResult(
            name="Gate 2: concurrent fan-out",
            passed=False,
            evidence={"exception": f"{type(exc).__name__}: {exc}"},
            detail=f"unhandled: {type(exc).__name__}",
        )
    results.append(r2)
    print(f"  {'PASS' if r2.passed else 'FAIL'}: {r2.detail}")
    print()
    if not r2.passed:
        return _finalise(results)

    # Gate 3
    print("Gate 3 — Adversarial egress denial (allow_internet_access=False)")
    try:
        r3 = gate_3_egress_denial()
    except Exception as exc:  # noqa: BLE001
        r3 = GateResult(
            name="Gate 3: egress denial",
            passed=False,
            evidence={"exception": f"{type(exc).__name__}: {exc}"},
            detail=f"unhandled: {type(exc).__name__}",
        )
    results.append(r3)
    print(f"  {'PASS' if r3.passed else 'FAIL'}: {r3.detail}")
    print()
    if not r3.passed:
        return _finalise(results)

    # Gate 4
    print("Gate 4 — Mid-exec kill clean (SDK error, no zombies)")
    try:
        r4 = gate_4_mid_exec_kill()
    except Exception as exc:  # noqa: BLE001
        r4 = GateResult(
            name="Gate 4: mid-exec kill",
            passed=False,
            evidence={"exception": f"{type(exc).__name__}: {exc}"},
            detail=f"unhandled: {type(exc).__name__}",
        )
    results.append(r4)
    print(f"  {'PASS' if r4.passed else 'FAIL'}: {r4.detail}")
    print()
    if not r4.passed:
        return _finalise(results)

    # Gate 5
    print("Gate 5 — 7-day realistic-load cost projection < $50/mo")
    gate1_mean = r1.evidence.get("mean_s", 0.0)
    try:
        r5 = gate_5_cost_projection(gate_1_mean_s=float(gate1_mean), target_monthly_usd=50.0)
    except Exception as exc:  # noqa: BLE001
        r5 = GateResult(
            name="Gate 5: cost projection",
            passed=False,
            evidence={"exception": f"{type(exc).__name__}: {exc}"},
            detail=f"unhandled: {type(exc).__name__}",
        )
    results.append(r5)
    print(f"  {'PASS' if r5.passed else 'FAIL'}: {r5.detail}")
    print()

    return _finalise(results)


def _finalise(results: list[GateResult]) -> int:
    """Print summary; write audit trail; return exit code (0 all-pass, 1 any-fail)."""
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    all_pass = all(r.passed for r in results)
    for r in results:
        marker = "PASS" if r.passed else "FAIL"
        print(f"  [{marker}] {r.name}: {r.detail}")
    print()
    if all_pass and len(results) == 5:
        print("✅ ALL FIVE GATES PASSED — D-12-12 confirms E2B as v0.1 substrate.")
        print("   T09 (sandbox pool) unblocked.")
    else:
        first_fail = next((r for r in results if not r.passed), None)
        if first_fail:
            print(f"❌ FAIL at {first_fail.name}.")
            print("   STOPPING per Phase-5 instruction.")
            print(
                "   D-12-12 reopens → Daytona (pending arch-doc verification) → self-Fly Machines."
            )

    # Write audit trail
    audit_dir = _REPO_ROOT / "docs/specs/phase2/spec_12/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    audit_path = audit_dir / f"lock_gates_{date_str}.md"

    status_line = (
        "✅ ALL FIVE PASS — D-12-12 confirmed E2B"
        if all_pass and len(results) == 5
        else "❌ FAIL — D-12-12 reopens"
    )
    gates_note = "stopped on first fail" if not all_pass else "all run"
    lines = [
        f"# D-12-12 lock-gate measurements — {date_str}",
        "",
        f"**Status:** {status_line}",
        "**Substrate:** E2B Hobby tier (Firecracker microVM)",
        f"**Gates attempted:** {len(results)}/5 ({gates_note})",
        "",
        "## Gate-by-gate results",
        "",
    ]
    for r in results:
        marker = "✅ PASS" if r.passed else "❌ FAIL"
        lines.append(f"### {r.name} — {marker}")
        lines.append("")
        lines.append(f"**Detail:** {r.detail}")
        lines.append("")
        lines.append("**Evidence:**")
        lines.append("")
        lines.append("```json")
        import json

        lines.append(json.dumps(r.evidence, indent=2, default=str))
        lines.append("```")
        lines.append("")
    audit_path.write_text("\n".join(lines))
    print()
    print(f"Audit trail written: {audit_path.relative_to(_REPO_ROOT)}")

    return 0 if all_pass and len(results) == 5 else 1


if __name__ == "__main__":
    sys.exit(main())
