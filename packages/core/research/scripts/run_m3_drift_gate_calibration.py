"""Replay the LLM-judge drift gate over the hand-authored drift-trajectory corpus.

Loads the in_persona + drifting conversations for each persona, replays
the gate on every (turn >= 1) of every conversation, and reports the
gate's flag-rate per condition at each candidate threshold. The corpus
shape (3 personas x 2 conditions x 6 user-assistant pairs, with user
turns identical between conditions) makes this a clean
flag-rate-differential test for gate quality.

Usage:
    uv run python scripts/run_m3_drift_gate_calibration.py \\
        --judge llama \\
        --thresholds 0.3,0.4,0.5,0.6,0.7 \\
        --output-dir results/m3_drift_gate_calibration/$(date +%Y%m%d_%H%M%S)

V100-only: loads Llama-3.1-8B (4-bit) as the gate judge.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.models import (
    GlmApiBackend,
    LlamaBackend,
    PrometheusBackend,
    QwenBackend,
)
from persona_rag.retrieval.base import Turn
from persona_rag.retrieval.drift_gate import LlmJudgeDriftGate
from persona_rag.schema.conversation import (
    DriftTrajectoryConversation,
    assert_user_turns_match,
)
from persona_rag.schema.persona import Persona

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIFT_TRAJECTORY_ROOT = REPO_ROOT / "benchmarks_data" / "drift_trajectory"


_JUDGE_SPECS: dict[str, dict[str, Any]] = {
    "llama": {
        "backend_cls": LlamaBackend,
        "model_id": "meta-llama/Llama-3.1-8B-Instruct",
        "name": "llama3.1-8b-instruct",
        "tier": "local",
    },
    "prometheus": {
        "backend_cls": PrometheusBackend,
        "model_id": "prometheus-eval/prometheus-7b-v2.0",
        "name": "prometheus-2-7b",
        "tier": "local",
    },
    "qwen2.5": {
        "backend_cls": QwenBackend,
        "model_id": "Qwen/Qwen2.5-7B-Instruct",
        "name": "qwen2.5-7b-instruct",
        "tier": "local",
    },
    "glm-api": {
        "backend_cls": GlmApiBackend,
        "model_id": "z-ai/glm4.7",
        "name": "glm-4.7",
        "tier": "api",
    },
}


def _build_judge(name: str) -> Any:
    """Build the gate judge backend.

    Supported judges (open-model only; API-tier judges require explicit
    project authorisation):

    - ``llama``: Meta-Llama-3.1-8B-Instruct (cheapest cross-family pick).
    - ``prometheus``: prometheus-eval/prometheus-7b-v2.0 (Mistral-Instruct
      base; evaluator-tuned).
    - ``qwen2.5``: Qwen/Qwen2.5-7B-Instruct (different family from Llama
      / Mistral / Gemma).
    """
    if name not in _JUDGE_SPECS:
        raise ValueError(f"--judge must be one of {sorted(_JUDGE_SPECS)}, got {name!r}")
    spec = _JUDGE_SPECS[name]
    backend_cls = spec["backend_cls"]
    # API-tier judges have no quantization / dtype knobs — instantiate
    # directly. The constructor reads the API key from the environment
    # (or a repo-root .env file) and never accepts it as an argument.
    if spec.get("tier") == "api":
        return backend_cls(model_id=spec["model_id"], name=spec["name"])
    # Local judges: use the backend's own ``default_config`` so per-model
    # attention / dtype overrides land (e.g. Qwen2.5 needs SDPA, not
    # eager — eager + fp16 + 4-bit produces NaN logits on V100).
    cfg = backend_cls.default_config(
        model_id=spec["model_id"],
        name=spec["name"],
        revision=None,
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        max_input_tokens=4096,
        trust_remote_code=False,
        warmup_nan_guard=True,
    )
    return backend_cls(cfg)


def _load_conversations(persona_id: str) -> dict[str, DriftTrajectoryConversation]:
    """Load the in_persona + drifting conversations for one persona."""
    base = DRIFT_TRAJECTORY_ROOT / persona_id
    convs = {
        "in_persona": DriftTrajectoryConversation.from_yaml(base / "in_persona.yaml"),
        "drifting": DriftTrajectoryConversation.from_yaml(base / "drifting.yaml"),
    }
    assert_user_turns_match(convs.values())
    return convs


def _gate_each_turn(
    *,
    persona: Persona,
    conv: DriftTrajectoryConversation,
    judge: Any,
    thresholds: list[float],
) -> list[dict[str, Any]]:
    """Replay the conversation and call the gate after each assistant turn.

    Skips the first turn (no prior assistant for the gate to judge).
    Returns one record per turn keyed by threshold so flag-rates can be
    computed in one pass.
    """
    history: list[Turn] = []
    out: list[dict[str, Any]] = []
    pairs = list(zip(conv.user_turn_texts(), conv.assistant_turn_texts(), strict=True))
    for turn_ix, (user_text, assistant_text) in enumerate(pairs):
        if turn_ix == 0:
            history.extend(
                [
                    Turn(role="user", content=user_text),
                    Turn(role="assistant", content=assistant_text),
                ]
            )
            continue
        history.extend(
            [
                Turn(role="user", content=user_text),
                Turn(role="assistant", content=assistant_text),
            ]
        )
        gate_default = LlmJudgeDriftGate(judge=judge, confidence_threshold=0.5)
        check = gate_default.check(persona=persona, query=user_text, history=history[:-1])
        record = {
            "persona_id": persona.persona_id,
            "condition": conv.condition,
            "turn_ix": turn_ix,
            "user_turn": user_text,
            "assistant_turn": assistant_text,
            "gate_flag": check.flag,
            "gate_confidence": check.confidence,
            "gate_rationale": check.rationale,
            "gate_template_version": check.template_version,
            "raw_response": check.raw_response,
            "drift_level_authored": _authored_drift(conv, turn_ix),
        }
        for t in thresholds:
            record[f"should_gate@{t}"] = check.flag == "drift" and check.confidence >= t
        out.append(record)
    return out


def _authored_drift(conv: DriftTrajectoryConversation, turn_ix: int) -> str | None:
    """Return the author-annotated drift_level for a given turn (drifting only)."""
    if conv.condition != "drifting":
        return None
    assistant_pos = turn_ix * 2 + 1
    if 0 <= assistant_pos < len(conv.turns):
        return conv.turns[assistant_pos].drift_level
    return None


def _summarise(records: list[dict[str, Any]], thresholds: list[float]) -> dict[str, Any]:
    """Compute flag-rate per (condition, threshold) and the differential.

    Includes the refined verdict (drifting subset = authored drift_level
    in {subtle, clear, break}) and the sharp verdict (drifting subset =
    authored drift_level in {clear, break}) so the report does not depend
    on a follow-up query against raw.json.
    """
    rates: dict[str, dict[str, float]] = {}
    for cond in ("in_persona", "drifting"):
        rates[cond] = {}
        cond_records = [r for r in records if r["condition"] == cond]
        n = len(cond_records)
        for t in thresholds:
            triggered = sum(1 for r in cond_records if r[f"should_gate@{t}"])
            rates[cond][f"{t}"] = triggered / n if n else 0.0
    differentials = {
        f"{t}": rates["drifting"][f"{t}"] - rates["in_persona"][f"{t}"] for t in thresholds
    }

    def _verdict_label(diff: float) -> str:
        if diff >= 0.5:
            return "confirmed (>=0.5)"
        if diff >= 0.4:
            return "weak (0.4-0.5)"
        if diff >= 0.3:
            return "below threshold (0.3-0.4)"
        return "refuted (<0.3)"

    verdict = {f"{t}": _verdict_label(differentials[f"{t}"]) for t in thresholds}

    # Refined verdict: drifting subset = authored drift_level >= subtle.
    drifting_authored = [
        r
        for r in records
        if r["condition"] == "drifting"
        and r.get("drift_level_authored") in ("subtle", "clear", "break")
    ]
    drifting_clear_break = [
        r
        for r in records
        if r["condition"] == "drifting" and r.get("drift_level_authored") in ("clear", "break")
    ]

    def _rate_at(rs: list[dict[str, Any]], t: float) -> float:
        return sum(1 for r in rs if r[f"should_gate@{t}"]) / len(rs) if rs else 0.0

    refined: dict[str, dict[str, float]] = {}
    sharp: dict[str, dict[str, float]] = {}
    for t in thresholds:
        ip_rate = rates["in_persona"][f"{t}"]
        refined_rate = _rate_at(drifting_authored, t)
        sharp_rate = _rate_at(drifting_clear_break, t)
        refined[f"{t}"] = {
            "drifting_flag_rate": refined_rate,
            "differential": refined_rate - ip_rate,
            "verdict": _verdict_label(refined_rate - ip_rate),
        }
        sharp[f"{t}"] = {
            "drifting_flag_rate": sharp_rate,
            "differential": sharp_rate - ip_rate,
            "verdict": _verdict_label(sharp_rate - ip_rate),
        }

    # Per-drift-level catch counts (computed at threshold 0.5 for the
    # headline; per-threshold breakdown is in raw.json).
    per_level: dict[str, dict[str, int]] = {}
    for level in ("in", "subtle", "clear", "break"):
        level_records = [
            r
            for r in records
            if r["condition"] == "drifting" and r.get("drift_level_authored") == level
        ]
        per_level[level] = {
            "total": len(level_records),
            "caught_at_0.5": sum(1 for r in level_records if r["should_gate@0.5"]),
        }

    # Axis-breakdown coverage + malformed rate (diagnoses whether the
    # judge is producing the structured JSON the v3 prompt asks for).
    axis_populated_count = 0
    malformed_count = 0
    for r in records:
        if "(malformed gate response" in (r.get("gate_rationale") or ""):
            malformed_count += 1
        # Try to parse axis breakdown from the raw_response JSON. The
        # parser drops it into rationale_match; for the report we re-derive
        # from raw to keep the diagnostic surface explicit.
        raw = r.get("raw_response", "")
        if any(
            f'"{axis}"' in raw
            for axis in (
                "self_facts_check",
                "worldview_check",
                "constraint_check",
                "epistemic_check",
            )
        ):
            axis_populated_count += 1

    return {
        "flag_rates": rates,
        "differential": differentials,
        "verdict": verdict,
        "refined_subtle_clear_break": refined,
        "sharp_clear_break": sharp,
        "per_authored_level": per_level,
        "axis_populated_count": axis_populated_count,
        "axis_total_records": len(records),
        "malformed_count": malformed_count,
    }


def main() -> int:
    """Calibration sweep entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--judge",
        default="llama",
        choices=sorted(_JUDGE_SPECS.keys()),
        help="Which open-model judge to use as the gate.",
    )
    parser.add_argument(
        "--thresholds",
        default="0.3,0.4,0.5,0.6,0.7",
        help="Comma-separated list of gate thresholds to sweep.",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--personas",
        default="cs_tutor,historian,climate_scientist",
        help="Comma-separated persona ids; default = all three shipped personas.",
    )
    args = parser.parse_args()

    thresholds = [float(t) for t in args.thresholds.split(",")]
    persona_ids = [p.strip() for p in args.personas.split(",") if p.strip()]
    out_root: Path = args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    judge = _build_judge(args.judge)
    all_records: list[dict[str, Any]] = []

    for pid in persona_ids:
        persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{pid}.yaml")
        convs = _load_conversations(pid)
        for cond, conv in convs.items():
            logger.info("running gate on persona={} condition={}", pid, cond)
            records = _gate_each_turn(
                persona=persona, conv=conv, judge=judge, thresholds=thresholds
            )
            all_records.extend(records)

    summary = _summarise(all_records, thresholds)

    (out_root / "raw.json").write_text(
        json.dumps({"records": all_records, "thresholds": thresholds}, indent=2),
        encoding="utf-8",
    )
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    headline_rows = [
        "## Headline (condition-level: in_persona vs drifting-as-authored)",
        "",
        "| threshold | in_persona flag-rate | drifting flag-rate | differential | verdict |",
        "|---|---|---|---|---|",
    ]
    for t in thresholds:
        ip = summary["flag_rates"]["in_persona"][f"{t}"]
        dr = summary["flag_rates"]["drifting"][f"{t}"]
        diff = summary["differential"][f"{t}"]
        verdict = summary["verdict"][f"{t}"]
        headline_rows.append(f"| {t:.2f} | {ip:.2%} | {dr:.2%} | {diff:+.2%} | {verdict} |")
    refined_rows = [
        "",
        "## Refined (drifting subset = authored drift_level in {subtle, clear, break})",
        "",
        "| threshold | drifting flag-rate | differential | verdict |",
        "|---|---|---|---|",
    ]
    for t in thresholds:
        sub = summary["refined_subtle_clear_break"][f"{t}"]
        refined_rows.append(
            f"| {t:.2f} | {sub['drifting_flag_rate']:.2%} | "
            f"{sub['differential']:+.2%} | {sub['verdict']} |"
        )
    sharp_rows = [
        "",
        "## Sharp (drifting subset = authored drift_level in {clear, break})",
        "",
        "| threshold | drifting flag-rate | differential | verdict |",
        "|---|---|---|---|",
    ]
    for t in thresholds:
        sh = summary["sharp_clear_break"][f"{t}"]
        sharp_rows.append(
            f"| {t:.2f} | {sh['drifting_flag_rate']:.2%} | "
            f"{sh['differential']:+.2%} | {sh['verdict']} |"
        )
    level_rows = [
        "",
        "## Per-authored-level catch rate (at threshold 0.5)",
        "",
        "| authored drift_level | caught / total |",
        "|---|---|",
    ]
    for level in ("in", "subtle", "clear", "break"):
        info = summary["per_authored_level"][level]
        total = info["total"]
        caught = info["caught_at_0.5"]
        pct = f" ({caught / total:.0%})" if total else ""
        level_rows.append(f"| {level} | {caught}/{total}{pct} |")
    diag_rows = [
        "",
        "## Diagnostics",
        "",
        f"- Total records: {summary['axis_total_records']}",
        f"- Records with axis-breakdown JSON populated: "
        f"{summary['axis_populated_count']}/{summary['axis_total_records']}",
        f"- Malformed-defaulted-to-ok records: "
        f"{summary['malformed_count']}/{summary['axis_total_records']}",
    ]
    (out_root / "report.md").write_text(
        "# Drift-gate calibration sweep\n\n"
        + "\n".join(headline_rows + refined_rows + sharp_rows + level_rows + diag_rows)
        + "\n",
        encoding="utf-8",
    )
    logger.info("calibration artefacts written to {}", out_root)

    per_persona = defaultdict(lambda: defaultdict(list))
    for rec in all_records:
        per_persona[rec["persona_id"]][rec["condition"]].append(rec)
    (out_root / "per_persona.json").write_text(
        json.dumps(
            {
                pid: {cond: len(records) for cond, records in conds.items()}
                for pid, conds in per_persona.items()
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
