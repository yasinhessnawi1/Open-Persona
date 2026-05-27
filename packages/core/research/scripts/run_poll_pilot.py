"""20-conversation PoLL panel pilot.

Loads 20 transcripts from a configurable source, scores each through
the 3-judge sequential pipeline (Prometheus-2-7B + Qwen2.5-7B +
Llama-3.1-8B), and writes per-judge checkpoints + a panel summary.

Per-judge score distributions and any malformed-output counts are
surfaced in the summary so the author can inspect whether one judge
is systematically harsher / softer than the others before scaling to
the full 150-item human-validation pipeline.

Usage:

    # Pilot from baseline-style response dirs (one mechanism, one persona).
    uv run python scripts/run_poll_pilot.py \\
        --source-dir results/run_xxx/responses \\
        --mechanism b1 \\
        --persona cs_tutor \\
        --max 20

    # Skip Prometheus from the panel (e.g. after micro-test failure).
    uv run python scripts/run_poll_pilot.py \\
        --source-dir results/run_xxx/responses \\
        --mechanism b1 --persona cs_tutor --max 20 \\
        --skip prometheus
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.evaluation.poll_panel import (
    JudgeSpec,
    PoLLPanel,
    load_checkpoints_from_dir,
    reliability_matrix_from_checkpoints,
    write_combined_summary,
)
from persona_rag.evaluation.transcripts import (
    load_baseline_response_dir,
    load_m3_records_json,
)
from persona_rag.schema.persona import Persona

REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_qwen():
    from persona_rag.models import QwenBackend

    cfg = QwenBackend.default_config(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        max_input_tokens=4096,
        trust_remote_code=False,
        warmup_nan_guard=True,
        revision=None,
    )
    return QwenBackend(cfg)


def _build_prometheus():
    from persona_rag.models import PrometheusBackend

    cfg = PrometheusBackend.default_config(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        max_input_tokens=4096,
        trust_remote_code=False,
        warmup_nan_guard=True,
        revision=None,
    )
    return PrometheusBackend(cfg)


def _build_llama():
    from persona_rag.models import LlamaBackend

    cfg = LlamaBackend.default_config(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        max_input_tokens=4096,
        trust_remote_code=False,
        warmup_nan_guard=True,
        revision=None,
    )
    return LlamaBackend(cfg)


# Token budgets: JSON judges (Qwen, Llama) emit a balanced JSON object in the
# first ~150 tokens; the rest is repeat-loop noise that the parser skips. Cap
# them at 256 to halve judge wall-clock without losing parses. Prometheus's
# native rubric is more variable in length and stays at 512.
#
# Soft-stop on `}` is the principled next step but requires extending the
# `LLMBackend.generate` protocol to take stop strings — out of scope for this
# patch. The token cap alone gets ~50% of the speedup.
_JSON_JUDGE_PERSONA_TOKENS = 256
_JSON_JUDGE_TASK_TOKENS = 192

_JUDGE_REGISTRY = {
    "prometheus": JudgeSpec(
        name="prometheus2_7b",
        builder=_build_prometheus,
        rubric_format="native_prometheus",
    ),
    "qwen": JudgeSpec(
        name="qwen2_5_7b",
        builder=_build_qwen,
        rubric_format="json",
        max_new_tokens_persona=_JSON_JUDGE_PERSONA_TOKENS,
        max_new_tokens_task=_JSON_JUDGE_TASK_TOKENS,
    ),
    "llama": JudgeSpec(
        name="llama3_1_8b",
        builder=_build_llama,
        rubric_format="json",
        max_new_tokens_persona=_JSON_JUDGE_PERSONA_TOKENS,
        max_new_tokens_task=_JSON_JUDGE_TASK_TOKENS,
    ),
}


def main() -> int:
    """Run the PoLL pilot. Returns 0 on success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        help="dir with response_*.json files (B1/B2/M1 shape). Required unless --records-json is set.",
    )
    parser.add_argument(
        "--records-json",
        help=(
            "path to a run_m3_vs_baselines_pilot.py records.json bundle. "
            "Mutually exclusive with --source-dir."
        ),
    )
    parser.add_argument("--mechanism", required=True, help="mechanism label (b1, b2, m1, m3)")
    parser.add_argument("--persona", required=True, help="persona id under personas/")
    parser.add_argument("--max", type=int, default=20, help="max conversations (default 20)")
    parser.add_argument(
        "--skip",
        nargs="*",
        choices=sorted(_JUDGE_REGISTRY.keys()),
        default=[],
        help="judges to skip from the panel",
    )
    parser.add_argument(
        "--out-root",
        default="results/poll_pilot",
        help="output root; a timestamped subdir is created",
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = REPO_ROOT / args.out_root / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.source_dir and not args.records_json:
        raise SystemExit("Provide either --source-dir or --records-json.")
    if args.source_dir and args.records_json:
        raise SystemExit("--source-dir and --records-json are mutually exclusive.")

    persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{args.persona}.yaml")
    if args.source_dir:
        convs = load_baseline_response_dir(
            Path(args.source_dir).resolve(),
            mechanism=args.mechanism,
            persona_id=args.persona,
        )
        source_label = args.source_dir
    else:
        convs = load_m3_records_json(
            Path(args.records_json).resolve(),
            mechanism=args.mechanism,
            persona_id=args.persona,
        )
        source_label = args.records_json
    convs = convs[: args.max]
    logger.info("PoLL pilot: {} conversations from {}", len(convs), source_label)

    judge_specs = [spec for name, spec in _JUDGE_REGISTRY.items() if name not in args.skip]
    panel = PoLLPanel(judges=judge_specs, output_dir=out_dir)
    results = panel.run(persona, convs)
    summary_path = write_combined_summary(out_dir, results)

    # Krippendorff's alpha -- only meaningful if 2+ judges actually scored.
    alpha_report: dict[str, Any] = {}
    if len(judge_specs) >= 2:
        try:
            import krippendorff
        except ImportError:
            logger.warning("krippendorff not installed -- skipping alpha report")
        else:
            ckpts = load_checkpoints_from_dir(out_dir)
            for rubric in ("persona_adherence", "task_quality"):
                matrix = reliability_matrix_from_checkpoints(ckpts, rubric=rubric)
                try:
                    alpha = float(
                        krippendorff.alpha(
                            reliability_data=matrix,
                            level_of_measurement="ordinal",
                        )
                    )
                except Exception as exc:
                    logger.warning("alpha {} failed: {}", rubric, exc)
                    alpha = float("nan")
                alpha_report[rubric] = alpha

    report_lines = [
        "# PoLL pilot report",
        f"timestamp: {timestamp}",
        f"persona: {args.persona}",
        f"mechanism: {args.mechanism}",
        f"n_conversations: {len(convs)}",
        f"judges: {[s.name for s in judge_specs]}",
        "",
        "## Headline scores",
        f"- persona_adherence (mean across judges, then conv): {results['poll_persona_adherence'].value:.3f}",
        f"- task_quality (mean across judges, then conv):      {results['poll_task_quality'].value:.3f}",
        "",
        "## Per-judge means",
    ]
    for rubric_key, label in [
        ("poll_persona_adherence", "persona_adherence"),
        ("poll_task_quality", "task_quality"),
    ]:
        per_judge = results[rubric_key].metadata.get("per_judge_aggregate", {})
        report_lines.append(f"### {label}")
        for jname, val in per_judge.items():
            report_lines.append(f"- {jname}: {val}")
        malformed_per = results[rubric_key].metadata.get("malformed_per_judge", {})
        report_lines.append(f"  malformed: {malformed_per}")
        report_lines.append("")

    if alpha_report:
        report_lines.append("## Krippendorff's alpha (panel inter-judge)")
        for rubric, alpha in alpha_report.items():
            report_lines.append(f"- {rubric}: alpha = {alpha:.3f}")

    (out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    (out_dir / "alpha.json").write_text(json.dumps(alpha_report, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "PoLL pilot done. summary={} report={}",
        summary_path,
        out_dir / "report.md",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
