"""Eyeball-check helper: pick a small random sample of gate decisions for review.

Reads the ``raw.json`` from a calibration run and prints a fixed, seeded
sample (3 drifting + 2 in_persona by default). For each record, dumps the
user turn, the assistant turn under evaluation, the gate's parsed
flag/confidence/rationale, and the raw judge response so the reviewer
can see whether the per-axis breakdown is populated and whether the
rationale is coherent given the conversation.

Per the project's open-judge sweep protocol, this is the mandatory
diagnostic step before reporting numbers from each judge: a judge that
emits structurally-valid JSON but never populates the per-axis
``violated`` fields is producing surface compliance, not real reasoning.

Usage:
    uv run python scripts/run_m3_eyeball_gate_samples.py \\
        --run-dir results/m3_drift_gate_calibration/<timestamp>_v2_prometheus27b \\
        --n-drifting 3 \\
        --n-in-persona 2 \\
        --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any


def _print_record(r: dict[str, Any]) -> None:
    """Pretty-print one record for eyeball review."""
    print("=" * 72)
    print(
        f"persona={r.get('persona_id')!r} condition={r.get('condition')!r} "
        f"turn={r.get('turn_ix')!r} authored={r.get('drift_level_authored')!r}"
    )
    print(
        f"gate flag={r.get('gate_flag')!r} confidence={r.get('gate_confidence')!r} "
        f"template_version={r.get('gate_template_version')!r}"
    )
    print(f"rationale: {r.get('gate_rationale')!r}")
    print()
    print("USER TURN:")
    print((r.get("user_turn") or "").strip())
    print()
    print("ASSISTANT TURN UNDER EVALUATION:")
    print((r.get("assistant_turn") or "").strip())
    print()
    print("RAW JUDGE RESPONSE:")
    print((r.get("raw_response") or "").strip())
    print()


def main() -> int:
    """Eyeball-check entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--run-dir",
        required=True,
        type=Path,
        help="Calibration run directory; reads run-dir/raw.json.",
    )
    parser.add_argument("--n-drifting", type=int, default=3, help="Drifting samples to print.")
    parser.add_argument(
        "--n-in-persona",
        type=int,
        default=2,
        help="In-persona samples to print.",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for sample selection.")
    args = parser.parse_args()

    raw_path: Path = args.run_dir / "raw.json"
    if not raw_path.exists():
        raise FileNotFoundError(f"{raw_path} not found")
    records = json.loads(raw_path.read_text(encoding="utf-8"))["records"]

    rng = random.Random(args.seed)
    drifting = [r for r in records if r["condition"] == "drifting"]
    in_persona = [r for r in records if r["condition"] == "in_persona"]
    rng.shuffle(drifting)
    rng.shuffle(in_persona)

    print(f"Eyeball sample from {raw_path} (seed={args.seed}):")
    print(
        f"  {min(args.n_drifting, len(drifting))} drifting + "
        f"{min(args.n_in_persona, len(in_persona))} in_persona records"
    )
    print()

    for r in drifting[: args.n_drifting]:
        _print_record(r)
    for r in in_persona[: args.n_in_persona]:
        _print_record(r)

    # Aggregate diagnostics so the reviewer can see whether per-axis
    # populated lines up across the full sweep, not just the eyeball
    # sample.
    n = len(records)
    axes = ("self_facts_check", "worldview_check", "constraint_check", "epistemic_check")
    axis_populated = sum(
        1 for r in records if any(f'"{axis}"' in (r.get("raw_response") or "") for axis in axes)
    )
    malformed = sum(
        1 for r in records if "(malformed gate response" in (r.get("gate_rationale") or "")
    )
    print("=" * 72)
    print("Sweep-wide diagnostics:")
    print(f"  total records:                                 {n}")
    print(f"  records mentioning per-axis fields in raw:     {axis_populated}/{n}")
    print(f"  records that defaulted to ok on malformed:     {malformed}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
