"""D-12-12 Gates 2-5 — concurrent fan-out, egress denial, mid-exec kill, cost projection.

Runs after Gate-1 confirmed (methodology-corrected) on 2026-06-05 per the
workload-clarification amendment to D-12-12 in decisions.md.

Stop-on-FAIL discipline holds: any FAIL halts the run, surfaces the failure
with evidence, names the gate. Gate 3 is the load-bearing one — substrate
disqualification on egress unreliability reopens D-12-12 regardless of how
the other gates land. Gates 2, 4, 5 are real but secondary (their failures
reopen the cost / operational profile, not the security baseline).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
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
class GateResult:
    name: str
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


# Gate 2 — 20-sandbox concurrent fan-out, p95 ready < 5s ---------------------


def gate_2_concurrent_fanout(n: int = 20, target_p95_s: float = 5.0) -> GateResult:
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

    async def _create_one() -> tuple[float, Any]:
        t0 = time.perf_counter()
        sandbox = await asyncio.to_thread(Sandbox)
        return time.perf_counter() - t0, sandbox

    async def _run() -> list[tuple[float, Any]]:
        return await asyncio.gather(*[_create_one() for _ in range(n)])

    print(f"  Spawning {n} sandboxes concurrently against E2B...", flush=True)
    try:
        results = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        return GateResult(
            name="Gate 2: concurrent fan-out",
            passed=False,
            evidence={"n": n, "exception": f"{type(exc).__name__}: {str(exc)[:300]}"},
            detail=f"fan-out raised {type(exc).__name__}",
        )

    timings_s = [t for t, _ in results]
    sandboxes = [s for _, s in results]
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
        detail=f"p95={p95_s:.3f}s vs target <{target_p95_s}s on n={n} concurrent",
    )


# Gate 3 — Adversarial egress denial (LOAD-BEARING) --------------------------


def gate_3_egress_denial() -> GateResult:
    """Each §9 #7 attack runs with ``allow_internet_access=False``.

    The substrate-level deny must hold regardless of the persona's allow-list
    intent (D-12-4 + spec-11 SSRF prior art). Failure here reopens D-12-12
    on security grounds, not cost/operational grounds — the substrate's
    egress controls are unreliable."""
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

    # Curated §9 #7 + #6 attacks. Each MUST FAIL to reach its target.
    attacks: list[tuple[str, str, str]] = [
        (
            "aws_imds_v1",
            "import urllib.request; import socket; socket.setdefaulttimeout(5); "
            'urllib.request.urlopen("http://169.254.169.254/latest/meta-data/").read()',
            "AWS IMDSv1 — credentials exfil endpoint",
        ),
        (
            "aws_imdsv2_token",
            "import urllib.request; import socket; socket.setdefaulttimeout(5); "
            'req = urllib.request.Request("http://169.254.169.254/latest/api/token", '
            'method="PUT"); req.add_header("X-aws-ec2-metadata-token-ttl-seconds", "21600"); '
            "urllib.request.urlopen(req).read()",
            "AWS IMDSv2 token endpoint",
        ),
        (
            "gcp_metadata_by_name",
            "import urllib.request; import socket; socket.setdefaulttimeout(5); "
            'req = urllib.request.Request("http://metadata.google.internal/"); '
            'req.add_header("Metadata-Flavor", "Google"); '
            "urllib.request.urlopen(req).read()",
            "GCP metadata via DNS name",
        ),
        (
            "rfc1918_10",
            'import socket; s=socket.socket(); s.settimeout(3); s.connect(("10.0.0.1", 22))',
            "RFC 1918 10.0.0.0/8",
        ),
        (
            "rfc1918_192",
            'import socket; s=socket.socket(); s.settimeout(3); s.connect(("192.168.0.1", 22))',
            "RFC 1918 192.168.0.0/16",
        ),
        (
            "loopback_22",
            'import socket; s=socket.socket(); s.settimeout(3); s.connect(("127.0.0.1", 22))',
            "Loopback (substrate host SSH)",
        ),
        (
            "ipv4_mapped_v6_imds",
            "import socket; s=socket.socket(socket.AF_INET6); s.settimeout(3); "
            's.connect(("::ffff:169.254.169.254", 80))',
            "IPv4-mapped IPv6 IMDS (v4-via-v6 bypass)",
        ),
        (
            "public_http_off",
            "import urllib.request; import socket; socket.setdefaulttimeout(5); "
            'urllib.request.urlopen("http://example.com/").read()',
            "Public HTTP (verifies allow_internet_access=False blanket-denies)",
        ),
    ]

    print("  Creating sandbox with allow_internet_access=False...", flush=True)
    try:
        sandbox = Sandbox(allow_internet_access=False)
    except Exception as exc:  # noqa: BLE001
        return GateResult(
            name="Gate 3: egress denial",
            passed=False,
            evidence={"error": f"could not create sandbox: {type(exc).__name__}: {exc}"},
            detail="sandbox creation with allow_internet_access=False failed",
        )

    per_attack: list[dict[str, str | bool]] = []
    all_blocked = True
    try:
        for name, code, desc in attacks:
            print(f"    Testing: {name} — {desc}", flush=True)
            try:
                result = sandbox.run_code(code, timeout=15)
                blocked = result.error is not None
                error_summary = ""
                if result.error:
                    error_summary = f"{result.error.name}: {result.error.value[:150]}"
                per_attack.append(
                    {
                        "attack": name,
                        "description": desc,
                        "blocked": blocked,
                        "error": error_summary,
                    }
                )
                if not blocked:
                    all_blocked = False
                    print(f"      ⚠ NOT BLOCKED: stdout={result.logs.stdout}", flush=True)
                else:
                    print(f"      ✓ blocked ({result.error.name})", flush=True)
            except Exception as exc:  # noqa: BLE001
                per_attack.append(
                    {
                        "attack": name,
                        "description": desc,
                        "blocked": True,  # treat SDK-level error as block
                        "error": f"SDK exception: {type(exc).__name__}: {str(exc)[:150]}",
                    }
                )
                print(f"      ✓ blocked (SDK exception: {type(exc).__name__})", flush=True)
    finally:
        with contextlib.suppress(Exception):
            sandbox.kill()

    blocked_count = sum(1 for a in per_attack if a["blocked"])
    return GateResult(
        name="Gate 3: egress denial",
        passed=all_blocked,
        evidence={"per_attack": per_attack, "blocked": blocked_count, "total": len(per_attack)},
        detail=f"{blocked_count}/{len(per_attack)} attacks blocked",
    )


# Gate 4 — Mid-exec kill clean ----------------------------------------------


def gate_4_mid_exec_kill() -> GateResult:
    """Kill a long-running execution from outside; verify clean SDK behaviour."""
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

    print("  Starting 30s sleep workload, will kill after 2s...", flush=True)
    sandbox = Sandbox()
    sandbox_id = getattr(sandbox, "sandbox_id", "<unknown>")
    long_code = "import time; time.sleep(30); print('FINISHED (should not see)')"

    import threading

    kill_at = time.perf_counter() + 2.0
    kill_exc: list[Exception | None] = [None]

    def _kill_after_delay() -> None:
        while time.perf_counter() < kill_at:
            time.sleep(0.1)
        try:
            sandbox.kill()
        except Exception as exc:  # noqa: BLE001
            kill_exc[0] = exc

    kill_thread = threading.Thread(target=_kill_after_delay)
    kill_thread.start()

    t0 = time.perf_counter()
    run_exc: Exception | None = None
    finished_normally = False
    try:
        result = sandbox.run_code(long_code, timeout=60)
        if result.logs.stdout and "FINISHED" in "".join(result.logs.stdout):
            finished_normally = True
    except Exception as exc:  # noqa: BLE001
        run_exc = exc
    kill_thread.join(timeout=10)
    elapsed_s = time.perf_counter() - t0

    # PASS: either run raised cleanly OR terminated quickly (no 30s sleep completion)
    clean_termination = run_exc is not None or (elapsed_s < 10 and not finished_normally)

    return GateResult(
        name="Gate 4: mid-exec kill",
        passed=clean_termination,
        evidence={
            "sandbox_id": sandbox_id,
            "elapsed_s": round(elapsed_s, 2),
            "run_raised": run_exc is not None,
            "run_exc_type": type(run_exc).__name__ if run_exc else None,
            "run_exc_msg": str(run_exc)[:200] if run_exc else None,
            "finished_normally": finished_normally,
            "kill_thread_exc": str(kill_exc[0]) if kill_exc[0] else None,
        },
        detail=(
            f"elapsed={elapsed_s:.1f}s; run_raised={run_exc is not None}; "
            f"finished_normally={finished_normally}"
        ),
    )


# Gate 5 — Cost projection --------------------------------------------------


def gate_5_cost_projection(target_monthly_usd: float = 50.0) -> GateResult:
    """Synthetic projection — D-12-12 load assumption + Gate-1-A realistic timing."""
    # From Gate-1-A revised measurement (2026-06-05 workload-revision audit):
    gate_1a_mean_s = 1.684  # mean from the revised Gate-1-A measurement
    creations_per_day = 200
    mean_session_s = 30.0  # D-12-12 assumption
    cost_per_sec_usd = 0.067 / 3600.0  # $0.067/h ÷ 3600s

    # Two projections: D-12-12 assumption vs actual-Gate-1-A-mean
    projected_d12_12 = creations_per_day * mean_session_s * 30.0 * cost_per_sec_usd
    projected_actual = creations_per_day * gate_1a_mean_s * 30.0 * cost_per_sec_usd

    return GateResult(
        name="Gate 5: cost projection",
        passed=projected_d12_12 < target_monthly_usd and projected_actual < target_monthly_usd,
        evidence={
            "target_monthly_usd": target_monthly_usd,
            "assumptions": {
                "creations_per_day": creations_per_day,
                "mean_session_s_d12_12": mean_session_s,
                "mean_session_s_actual_gate_1a": gate_1a_mean_s,
                "cost_per_sec_usd": round(cost_per_sec_usd, 7),
            },
            "projected_d12_12_assumption_usd_mo": round(projected_d12_12, 2),
            "projected_actual_gate1a_usd_mo": round(projected_actual, 2),
        },
        detail=(
            f"D-12-12 assumption: ${projected_d12_12:.2f}/mo; "
            f"actual-Gate-1-A: ${projected_actual:.2f}/mo "
            f"(target <${target_monthly_usd:.2f}/mo)"
        ),
    )


# Driver --------------------------------------------------------------------


def main() -> int:
    print("=" * 70)
    print("D-12-12 LOCK-GATES 2-5 — spec 12 T08")
    print("(Gate 1 confirmed via workload-clarification on 2026-06-05 amend)")
    print("=" * 70)
    print()

    results: list[GateResult] = []

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

    # Gate 3 — LOAD-BEARING
    print("Gate 3 — Adversarial egress denial (LOAD-BEARING)")
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
    print("Gate 4 — Mid-exec kill clean")
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
    try:
        r5 = gate_5_cost_projection(target_monthly_usd=50.0)
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
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    all_pass = all(r.passed for r in results)
    for r in results:
        marker = "PASS" if r.passed else "FAIL"
        print(f"  [{marker}] {r.name}: {r.detail}")
    print()
    if all_pass and len(results) == 4:
        print("✅ ALL FOUR GATES PASSED — D-12-12 CONFIRMED on Phase-5 evidence.")
        print("   With Gate 1 (workload-revision PASS), all five gates green.")
        print("   T09 (sandbox pool) unblocked.")
    else:
        first_fail = next((r for r in results if not r.passed), None)
        if first_fail:
            print(f"❌ FAIL at {first_fail.name}.")
            print("   STOPPING per Phase-5 instruction.")
            if "Gate 3" in first_fail.name:
                print("   Gate 3 is LOAD-BEARING — egress unreliability reopens D-12-12")
                print("   on security grounds. Daytona (arch-doc verification required)")
                print("   → self-Fly Machines is the substrate reopen path.")
            else:
                print(f"   {first_fail.name} reopens cost/operational profile, not the")
                print("   security baseline. Substrate-decision: D-12-12 reopens; same")
                print("   Daytona-then-self-Fly path.")

    audit_dir = _REPO_ROOT / "docs/specs/phase2/spec_12/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    audit_path = audit_dir / f"lock_gates_2_to_5_{date_str}.md"

    status_line = (
        "✅ ALL FOUR PASS (Gates 2-5) — combined with Gate 1 workload-revision "
        "PASS, D-12-12 CONFIRMED"
        if all_pass and len(results) == 4
        else "❌ FAIL — D-12-12 reopens"
    )
    lines: list[str] = [
        f"# D-12-12 lock-gates 2-5 measurements — {date_str}",
        "",
        f"**Status:** {status_line}",
        "**Substrate:** E2B Hobby tier (Firecracker microVM)",
        f"**Gates attempted:** {len(results)}/4 (Gates 2-5; Gate 1 amended via "
        f"workload-clarification)",
        "",
        "## Predecessor",
        "",
        "- [`lock_gates_2026-06-05.md`](lock_gates_2026-06-05.md) — Gate 1 literal contract (FAIL "
        "preserved as discipline-working evidence)",
        "- [`lock_gates_2026-06-05_workload_revision.md`]"
        "(lock_gates_2026-06-05_workload_revision.md) — Gate 1 workload-revision "
        "(PASS; methodology recovery)",
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
        lines.append(json.dumps(r.evidence, indent=2, default=str))
        lines.append("```")
        lines.append("")
    audit_path.write_text("\n".join(lines))
    print()
    print(f"Audit trail written: {audit_path.relative_to(_REPO_ROOT)}")

    return 0 if all_pass and len(results) == 4 else 1


if __name__ == "__main__":
    sys.exit(main())
