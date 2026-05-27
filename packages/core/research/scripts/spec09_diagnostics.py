"""Spec-09 close-out diagnostics — the three follow-up items from decision #070.

Reads existing Spec-09 sweep + harness artefacts, no model load, no
regeneration. Three sub-commands:

  diagnose-gate    — count gate_should_gate=True/False/missing across
                     M3 transcripts to separate "plumbing bug" from
                     "gate-never-fired" finding.

  minicheck-spot   — print 5 random (sentence, self_fact, p_supported)
                     triples from M1 contradicted sentences; needs
                     MiniCheck scorer (CPU is fine).

  per-probe-type   — slice PoLL persona-adherence + MiniCheck per
                     probe type (A/B/C) per mechanism.

Usage::

    uv run python scripts/spec09_diagnostics.py diagnose-gate \\
        --sweep-dir results/spec09_full_sweep/20260430_122208

    uv run python scripts/spec09_diagnostics.py minicheck-spot \\
        --sweep-dir results/spec09_full_sweep/20260430_122208 \\
        --persona cs_tutor --device cuda --n 5

    uv run python scripts/spec09_diagnostics.py per-probe-type \\
        --sweep-dir results/spec09_full_sweep/20260430_122208 \\
        --harness-dir results/spec09_harness/20260501_061045 \\
        --probes-root benchmarks_data/counterfactual_probes
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PERSONAS = ("cs_tutor", "historian", "climate_scientist")


# ---------------------------------------------------------------- helpers


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_transcript_metadata(path: Path) -> dict[str, Any]:
    """Return the loaded transcript JSON (turns + per_turn_metadata)."""
    return _read_json(path)


def _gate_value_from_meta(turn_meta: dict[str, Any]) -> bool | None:
    """Read ``gate_should_gate`` from a per-turn metadata dict.

    Mirrors the metric-side flatten logic: top-level key wins, then
    ``metadata`` nest, then ``pipeline_metadata`` nest. Returns None
    when no key is present anywhere.
    """
    if "gate_should_gate" in turn_meta:
        return bool(turn_meta["gate_should_gate"])
    for nest_key in ("metadata", "pipeline_metadata"):
        nested = turn_meta.get(nest_key)
        if isinstance(nested, dict) and "gate_should_gate" in nested:
            return bool(nested["gate_should_gate"])
    return None


# ---------------------------------------------------------------- diagnose-gate


def diagnose_gate(sweep_dir: Path, *, personas: tuple[str, ...]) -> int:
    """Count gate_should_gate=True/False/missing across M3 transcripts."""
    m3_dir = sweep_dir / "transcripts" / "M3"
    if not m3_dir.exists():
        print(f"ERROR: M3 transcripts dir not found at {m3_dir}", file=sys.stderr)
        return 1

    print(f"===== diagnose-gate over {m3_dir} =====")
    total: dict[str, int] = defaultdict(int)
    by_persona: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for path in sorted(m3_dir.glob("*.json")):
        payload = _load_transcript_metadata(path)
        persona_id = payload.get("persona_id", "<unknown>")
        if personas and persona_id not in personas:
            continue
        for turn_meta in payload.get("per_turn_metadata", []):
            value = _gate_value_from_meta(turn_meta)
            label = "missing" if value is None else ("true" if value else "false")
            total[label] += 1
            by_persona[persona_id][label] += 1

    print(f"  M3 turns total: {sum(total.values())}")
    print(f"  gate_should_gate=True   : {total['true']}")
    print(f"  gate_should_gate=False  : {total['false']}")
    print(f"  gate_should_gate=missing: {total['missing']}")
    print()
    for persona_id in sorted(by_persona):
        c = by_persona[persona_id]
        n = sum(c.values())
        rate = c["true"] / n if n else 0.0
        print(
            f"  {persona_id:18s} true={c['true']:3d} false={c['false']:3d} "
            f"missing={c['missing']:3d} trigger_rate={rate:.3f}"
        )

    print()
    if total["missing"] > 0 and total["true"] == 0 and total["false"] == 0:
        print("VERDICT: PLUMBING BUG — gate signal not in transcripts at all.")
        print("Check ProbeRunner per-turn metadata emission.")
    elif total["missing"] > 0:
        print("VERDICT: PARTIAL — some turns missing gate_should_gate.")
        print("Mixed evidence; investigate turn-level emission.")
    elif total["true"] == 0:
        print("VERDICT: REAL FINDING — gate_should_gate present but never True.")
        print("M3's gate genuinely did not fire on any of these turns.")
        print("M3's cost premium is not justified on this benchmark.")
    else:
        print(f"VERDICT: GATE FIRED on {total['true']}/{sum(total.values())} turns.")
        print("Re-run drift_quality + cost scoring with the fixed plumbing.")
    return 0


# ---------------------------------------------------------------- per-probe-type


def per_probe_type(
    sweep_dir: Path,
    harness_dir: Path,
    probes_root: Path,
    *,
    personas: tuple[str, ...],
) -> int:
    """Slice PoLL persona-adherence + MiniCheck per (mechanism, probe_type)."""
    # Build conversation_id -> probe_type lookup from the on-disk probe suite.
    # (We avoid importing the loader to keep this script dep-free.)
    conv_to_type: dict[str, str] = {}
    for persona_dir in sorted(probes_root.iterdir()):
        if not persona_dir.is_dir() or persona_dir.name == "chunks":
            continue
        for probe_path in sorted(persona_dir.glob("*.yaml")):
            try:
                # Cheap parse — we only need probe_type and the per-conversation id.
                import yaml

                raw = yaml.safe_load(probe_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"WARN: skip {probe_path}: {exc}", file=sys.stderr)
                continue
            persona_id = raw.get("persona_id")
            probe_id = raw.get("probe_id") or probe_path.stem
            probe_type = raw.get("probe_type")
            if persona_id and probe_id and probe_type:
                conv_id = f"{persona_id}::{probe_id}"
                conv_to_type[conv_id] = probe_type

    print(f"  loaded {len(conv_to_type)} conversation -> probe_type mappings")

    # Read harness results.json to get per-conversation PoLL + MiniCheck.
    results = _read_json(harness_dir / "results.json")

    # Aggregate per (mechanism, probe_type) for the headline metrics.
    poll_by_mtype: dict[tuple[str, str], list[float]] = defaultdict(list)
    minicheck_by_mtype: dict[tuple[str, str], list[float]] = defaultdict(list)

    for cell in results.get("cells", []):
        mech = cell.get("mechanism", "?")
        persona_id = cell.get("persona_id", "?")
        if personas and persona_id not in personas:
            continue
        for m in cell.get("metrics", []):
            name = m.get("name", "")
            per_conv = m.get("per_conversation") or []
            per_conv_ids = m.get("per_conversation_ids") or []
            for cid, val in zip(per_conv_ids, per_conv, strict=False):
                # cid in harness is e.g. "cs_tutor::B2::cs_tutor::probe_b_01" —
                # strip the runner-emitted prefix to recover the suite-side id.
                stripped = _strip_runner_prefix(cid)
                ptype = conv_to_type.get(stripped)
                if ptype is None:
                    continue
                key = (mech, ptype)
                if name == "poll_persona_adherence":
                    poll_by_mtype[key].append(float(val))
                elif name == "minicheck_self_fact_contradiction":
                    minicheck_by_mtype[key].append(float(val))

    # PoLL table
    print()
    print("===== PoLL persona-adherence per (mechanism, probe_type) =====")
    print(f"{'Mechanism':10s} {'Type A':>10s} {'Type B':>10s} {'Type C':>10s} {'Overall':>10s}")
    for mech in ("B1", "B2", "M1", "M3"):
        row = [mech]
        all_vals: list[float] = []
        for ptype in ("self_fact_challenge", "counterfactual", "constraint_bait"):
            vals = poll_by_mtype.get((mech, ptype), [])
            all_vals.extend(vals)
            row.append(f"{(sum(vals) / len(vals)) if vals else float('nan'):.3f} (n={len(vals)})")
        overall = sum(all_vals) / len(all_vals) if all_vals else float("nan")
        row.append(f"{overall:.3f} (n={len(all_vals)})")
        print(f"{row[0]:10s} {row[1]:>18s} {row[2]:>18s} {row[3]:>18s} {row[4]:>18s}")

    # MiniCheck table
    print()
    print("===== MiniCheck per (mechanism, probe_type) =====")
    print(f"{'Mechanism':10s} {'Type A':>10s} {'Type B':>10s} {'Type C':>10s} {'Overall':>10s}")
    for mech in ("B1", "B2", "M1", "M3"):
        row = [mech]
        all_vals: list[float] = []
        for ptype in ("self_fact_challenge", "counterfactual", "constraint_bait"):
            vals = minicheck_by_mtype.get((mech, ptype), [])
            all_vals.extend(vals)
            row.append(f"{(sum(vals) / len(vals)) if vals else float('nan'):.3f} (n={len(vals)})")
        overall = sum(all_vals) / len(all_vals) if all_vals else float("nan")
        row.append(f"{overall:.3f} (n={len(all_vals)})")
        print(f"{row[0]:10s} {row[1]:>18s} {row[2]:>18s} {row[3]:>18s} {row[4]:>18s}")
    return 0


def _strip_runner_prefix(cid: str) -> str:
    """Map runner-id ``cs_tutor::B2::cs_tutor::probe_b_01`` → suite-id ``cs_tutor::probe_b_01``.

    The runner emits ``f'{persona_id}::{mechanism}::{conv.conversation_id}'`` and
    the conv.conversation_id from the loader is itself ``f'{persona_id}::{probe_id}'``.
    Stripping the first two ``::`` segments recovers the suite-side id used by
    the on-disk probe YAML mapping.
    """
    parts = cid.split("::", 2)
    if len(parts) < 3:
        return cid
    return parts[2]


# ---------------------------------------------------------------- minicheck-spot


def minicheck_spot(
    sweep_dir: Path,
    *,
    persona_id: str,
    n_samples: int,
    device: str,
    seed: int,
) -> int:
    """Print N random (sentence, self_fact, p_supported) triples from M1 contradicted set."""
    from persona_rag.evaluation.minicheck_metric import (
        HFMiniCheckScorer,
        is_persona_relevant,
        split_sentences,
    )
    from persona_rag.schema.persona import Persona

    persona_path = REPO_ROOT / "personas" / f"{persona_id}.yaml"
    persona = Persona.from_yaml(persona_path)
    self_facts = [sf.fact for sf in persona.self_facts]

    m1_dir = sweep_dir / "transcripts" / "M1"
    convs = sorted(m1_dir.glob(f"{persona_id}__*.json"))
    if not convs:
        print(f"ERROR: no M1 transcripts for {persona_id} under {m1_dir}", file=sys.stderr)
        return 1

    print(f"===== minicheck-spot: persona={persona_id} n={n_samples} device={device} =====")
    print(f"  loaded {len(convs)} M1 transcripts; {len(self_facts)} self-facts")

    # Walk every assistant turn, score every persona-relevant sentence, collect
    # the (sentence, self_fact, p_supported) triples that the metric flags as
    # contradicted (max p_supported across self-facts < 0.5).
    print(f"  loading MiniCheck scorer (device={device})...")
    scorer = HFMiniCheckScorer(device=device)
    print("  scorer loaded; scanning turns...")

    contradicted_triples: list[tuple[str, str, float]] = []
    n_turns_scanned = 0
    n_relevant_scanned = 0

    for path in convs:
        payload = _load_transcript_metadata(path)
        for turn in payload.get("turns", []):
            n_turns_scanned += 1
            assistant = turn.get("assistant_text", "")
            for sentence in split_sentences(assistant):
                if not is_persona_relevant(sentence):
                    continue
                n_relevant_scanned += 1
                # For each self-fact, get p_supported; record max.
                p_each = []
                for sf in self_facts:
                    score = scorer.score(claim=sentence, document=sf)
                    p_each.append((sf, score))
                max_p = max(p for _, p in p_each)
                if max_p < 0.5:
                    # Contradicted. Record the *best-supporting* self-fact alongside.
                    best_sf, best_p = max(p_each, key=lambda kv: kv[1])
                    contradicted_triples.append((sentence, best_sf, best_p))

    print(
        f"  scanned {n_turns_scanned} turns; {n_relevant_scanned} persona-relevant sentences; "
        f"{len(contradicted_triples)} flagged as contradicted"
    )

    if not contradicted_triples:
        print("  no contradicted sentences found — nothing to sample")
        return 0

    rng = random.Random(seed)
    sample = rng.sample(contradicted_triples, min(n_samples, len(contradicted_triples)))

    print()
    for i, (sentence, best_sf, best_p) in enumerate(sample, 1):
        print(f"--- sample {i}/{len(sample)} ---")
        print(f"  sentence       : {sentence}")
        print(f"  best self_fact : {best_sf}")
        print(f"  max p_supported: {best_p:.3f}  (< 0.5 = contradicted)")
        print()
    print("Manual classification: for each sample above, mark REAL or FP.")
    print("REAL = sentence makes a persona claim that contradicts a self-fact.")
    print("FP   = disclaimer / hedge / unrelated content the gate missed.")
    return 0


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_diag = sub.add_parser("diagnose-gate", help="count gate_should_gate values across M3")
    p_diag.add_argument("--sweep-dir", type=Path, required=True)
    p_diag.add_argument("--personas", default=",".join(DEFAULT_PERSONAS))

    p_spot = sub.add_parser("minicheck-spot", help="N random contradicted triples from M1")
    p_spot.add_argument("--sweep-dir", type=Path, required=True)
    p_spot.add_argument("--persona", required=True)
    p_spot.add_argument("--n", type=int, default=5)
    p_spot.add_argument("--device", default="cpu")
    p_spot.add_argument("--seed", type=int, default=42)

    p_pt = sub.add_parser("per-probe-type", help="PoLL + MiniCheck per (mechanism, probe_type)")
    p_pt.add_argument("--sweep-dir", type=Path, required=True)
    p_pt.add_argument("--harness-dir", type=Path, required=True)
    p_pt.add_argument(
        "--probes-root",
        type=Path,
        default=REPO_ROOT / "benchmarks_data" / "counterfactual_probes",
    )
    p_pt.add_argument("--personas", default=",".join(DEFAULT_PERSONAS))

    args = parser.parse_args()

    if args.cmd == "diagnose-gate":
        personas = tuple(p.strip() for p in args.personas.split(",") if p.strip())
        return diagnose_gate(args.sweep_dir, personas=personas)
    if args.cmd == "minicheck-spot":
        return minicheck_spot(
            args.sweep_dir,
            persona_id=args.persona,
            n_samples=args.n,
            device=args.device,
            seed=args.seed,
        )
    if args.cmd == "per-probe-type":
        personas = tuple(p.strip() for p in args.personas.split(",") if p.strip())
        return per_probe_type(args.sweep_dir, args.harness_dir, args.probes_root, personas=personas)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
