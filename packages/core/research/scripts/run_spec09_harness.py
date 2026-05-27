"""Score Spec-09 full-sweep transcripts through the Spec-8 metric stack.

Reads transcripts written by ``scripts/run_spec09_full_sweep.py``, loads
them as ``EvalConversation`` objects, and runs the Spec-8
``EvaluationRunner`` with the canonical metric stack:

- ``MiniCheckMetric`` — primary self-fact contradiction (FT5 NLI).
- ``SyconMetric`` — worldview ToF/NoF flip rate (Qwen2.5-7B stance judge).
- ``PoLLPanel`` adapter (persona-adherence + task-quality, 3-judge sequential).
- ``CostTracker`` per mechanism.
- ``DriftQualityMetric`` for M3 only (precision / recall / F1 of the gate
  against MiniCheck-derived inconsistency labels).

Output: the standard Spec-8 ``results.csv`` + ``results_aggregate.csv`` +
``results.json`` + ``run_config.json``, plus a report.md that pulls out
the headline counterfactual-probes table the report consumes.

Usage::

    uv run python scripts/run_spec09_harness.py \\
        --run-dir results/spec09_full_sweep/<sweep_run_id> \\
        --out-root results/spec09_harness
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.evaluation import (
    CostTracker,
    DriftQualityMetric,
    EvaluationRunner,
    MechanismCell,
    Metric,
    MiniCheckMetric,
    SyconMetric,
)
from persona_rag.evaluation.metrics import EvalConversation, ScoredTurn
from persona_rag.evaluation.minicheck_metric import HFMiniCheckScorer
from persona_rag.evaluation.poll_panel import JudgeSpec, PoLLPanel
from persona_rag.evaluation.sycon_metric import LlmStanceClassifier
from persona_rag.schema.persona import Persona

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PERSONAS = ("cs_tutor", "historian", "climate_scientist")
DEFAULT_MECHANISMS = ("B1", "B2", "M1", "M3")


def _load_transcript(path: Path) -> EvalConversation:
    raw = json.loads(path.read_text(encoding="utf-8"))
    turns = tuple(
        ScoredTurn(
            turn_index=int(t["turn_index"]),
            user_text=str(t["user_text"]),
            assistant_text=str(t["assistant_text"]),
        )
        for t in raw["turns"]
    )
    per_turn_metadata = tuple(raw.get("per_turn_metadata") or [])
    return EvalConversation(
        conversation_id=str(raw["conversation_id"]),
        mechanism=str(raw["mechanism"]),
        persona_id=str(raw["persona_id"]),
        turns=turns,
        per_turn_metadata=per_turn_metadata,
    )


def _git_rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, encoding="utf-8"
        ).strip()
    except Exception:
        return "unknown"


def _build_metrics(
    *,
    enable_minicheck: bool,
    enable_sycon: bool,
    enable_poll: bool,
    enable_drift_quality: bool,
    mechanism_labels: list[str],
    out_dir: Path,
    minicheck_device: str,
) -> list[Metric]:
    """Build the canonical Spec-8 metric stack.

    Mirrors the wiring in ``scripts/run_evaluation_harness.py`` (Spec-8 close-out)
    so the metric semantics are identical: same MiniCheck FT5 scorer, same
    Qwen2.5-7B SYCON stance judge, same PoLL panel (Prometheus + Qwen +
    Llama, sequential loading + per-cell checkpoint subdirs from
    decision #065), same DriftQualityMetric sharing the MiniCheck scorer,
    same per-mechanism CostTracker.
    """
    metrics: list[Metric] = []

    minicheck_scorer = None
    if enable_minicheck or enable_drift_quality:
        minicheck_scorer = HFMiniCheckScorer(device=minicheck_device)
    if enable_minicheck:
        metrics.append(MiniCheckMetric(scorer=minicheck_scorer))

    if enable_sycon:
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
        backend = QwenBackend(cfg)
        metrics.append(SyconMetric(classifier=LlmStanceClassifier(judge=backend)))

    if enable_poll:

        def _build_qwen():
            from persona_rag.models import QwenBackend

            return QwenBackend(
                QwenBackend.default_config(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    max_input_tokens=4096,
                    trust_remote_code=False,
                    warmup_nan_guard=True,
                    revision=None,
                )
            )

        def _build_prometheus():
            from persona_rag.models import PrometheusBackend

            return PrometheusBackend(
                PrometheusBackend.default_config(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    max_input_tokens=4096,
                    trust_remote_code=False,
                    warmup_nan_guard=True,
                    revision=None,
                )
            )

        def _build_llama():
            from persona_rag.models import LlamaBackend

            return LlamaBackend(
                LlamaBackend.default_config(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    max_input_tokens=4096,
                    trust_remote_code=False,
                    warmup_nan_guard=True,
                    revision=None,
                )
            )

        _judge_specs = [
            JudgeSpec(
                name="prometheus2_7b",
                builder=_build_prometheus,
                rubric_format="native_prometheus",
            ),
            JudgeSpec(
                name="qwen2_5_7b",
                builder=_build_qwen,
                rubric_format="json",
                max_new_tokens_persona=256,
                max_new_tokens_task=192,
            ),
            JudgeSpec(
                name="llama3_1_8b",
                builder=_build_llama,
                rubric_format="json",
                max_new_tokens_persona=256,
                max_new_tokens_task=192,
            ),
        ]

        poll_output_dir = out_dir / "poll"
        poll_output_dir.mkdir(parents=True, exist_ok=True)

        class _PoLLAsTwoMetrics:
            """Per-cell PoLL panel adapter — same shape as Spec-8 (decision #065)."""

            def __init__(self) -> None:
                self.name = "poll_panel"
                self._cache: dict[str, dict[str, Any]] = {}

            @staticmethod
            def _cache_key(conversations, persona) -> str:
                ids = "|".join(c.conversation_id for c in conversations)
                conv_hash = hashlib.sha256(ids.encode("utf-8")).hexdigest()[:12]
                return f"{persona.persona_id or '<unknown>'}::{conv_hash}"

            def _run_for_cell(self, conversations, persona) -> dict[str, Any]:
                key = self._cache_key(conversations, persona)
                if key in self._cache:
                    return self._cache[key]
                cell_dir = poll_output_dir / key.replace("::", "_")
                panel = PoLLPanel(judges=_judge_specs, output_dir=cell_dir)
                self._cache[key] = panel.run(persona, conversations)
                return self._cache[key]

            def score(self, conversations, persona):
                return self._run_for_cell(conversations, persona)["poll_persona_adherence"]

        class _PoLLTaskQuality:
            def __init__(self, parent: _PoLLAsTwoMetrics) -> None:
                self._parent = parent
                self.name = "poll_task_quality"

            def score(self, conversations, persona):
                return self._parent._run_for_cell(conversations, persona)["poll_task_quality"]

        adapter = _PoLLAsTwoMetrics()
        metrics.append(adapter)
        metrics.append(_PoLLTaskQuality(adapter))

    if enable_drift_quality and "M3" in mechanism_labels:
        if minicheck_scorer is None:
            raise SystemExit("--no-minicheck conflicts with drift_quality (shares scorer)")
        metrics.append(DriftQualityMetric(scorer=minicheck_scorer))

    # CostTracker is per-mechanism — runner skips cells where mechanism mismatches.
    for mechanism in mechanism_labels:
        metrics.append(CostTracker(mechanism=mechanism))

    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Spec-09 full-sweep run directory (contains transcripts/).",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=REPO_ROOT / "results" / "spec09_harness",
    )
    parser.add_argument("--personas", default=",".join(DEFAULT_PERSONAS))
    parser.add_argument("--mechanisms", default=",".join(DEFAULT_MECHANISMS))
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--no-minicheck", action="store_true")
    parser.add_argument("--no-sycon", action="store_true")
    parser.add_argument("--no-poll", action="store_true")
    parser.add_argument("--no-drift-quality", action="store_true")
    parser.add_argument(
        "--minicheck-device",
        default="cuda",
        help="Device for HFMiniCheckScorer (cuda or cpu). Default: cuda.",
    )
    args = parser.parse_args()

    sweep_dir: Path = args.run_dir
    if not sweep_dir.exists():
        raise FileNotFoundError(f"sweep run dir not found: {sweep_dir}")
    transcripts_root = sweep_dir / "transcripts"
    if not transcripts_root.exists():
        raise FileNotFoundError(f"transcripts dir not found: {transcripts_root}")

    persona_ids = [p.strip() for p in args.personas.split(",") if p.strip()]
    mechanism_labels = [m.strip() for m in args.mechanisms.split(",") if m.strip()]
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir: Path = args.out_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build cells: one (mechanism, persona) per available transcript dir.
    cells: list[MechanismCell] = []
    for persona_id in persona_ids:
        persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{persona_id}.yaml")
        for mechanism in mechanism_labels:
            mech_dir = transcripts_root / mechanism
            if not mech_dir.exists():
                logger.warning("no transcripts dir for {} - skipping cell", mechanism)
                continue
            paths = sorted(mech_dir.glob(f"{persona_id}__*.json"))
            if not paths:
                logger.warning(
                    "no transcripts found for ({}, {}) - skipping",
                    mechanism,
                    persona_id,
                )
                continue
            convs = [_load_transcript(p) for p in paths]
            cells.append(
                MechanismCell(
                    mechanism=mechanism,
                    model="gemma2-9b-it",
                    benchmark="counterfactual_probes",
                    persona=persona,
                    conversations=convs,
                    seed=42,
                )
            )

    if not cells:
        raise RuntimeError(f"no cells assembled from {transcripts_root}; check personas/mechanisms")

    metrics = _build_metrics(
        enable_minicheck=not args.no_minicheck,
        enable_sycon=not args.no_sycon,
        enable_poll=not args.no_poll,
        enable_drift_quality=not args.no_drift_quality,
        mechanism_labels=mechanism_labels,
        out_dir=out_dir,
        minicheck_device=args.minicheck_device,
    )

    runner = EvaluationRunner(
        output_dir=out_dir,
        metrics=metrics,
        run_id=run_id,
        seed=42,
    )

    logger.info(
        "Spec-09 harness: scoring {} cells x {} metrics",
        len(cells),
        len(metrics),
    )
    runner.run(cells)

    # Headline report: pull aggregates from results_aggregate.csv into a
    # report.md the author can read directly.
    aggregate_path = out_dir / "results_aggregate.csv"
    report_lines = [
        f"# Spec-09 full-sweep harness scoring -- run {run_id}",
        "",
        f"- sweep run dir: {sweep_dir}",
        f"- mechanisms: {mechanism_labels}",
        f"- personas: {persona_ids}",
        f"- cells scored: {len(cells)}",
        f"- metrics: {[m.name for m in metrics]}",
        "",
        "## Headline counterfactual-probes table",
        "",
        "Source: `results_aggregate.csv` (one row per (mechanism, persona, metric)).",
        "Read alongside `results.json` for per-conversation distributions and metric metadata.",
        "",
    ]
    if aggregate_path.exists():
        report_lines.append("```")
        report_lines.append(aggregate_path.read_text(encoding="utf-8").strip())
        report_lines.append("```")
    (out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    # Run config snapshot.
    config = {
        "run_id": run_id,
        "git_rev": _git_rev(),
        "timestamp": datetime.now().isoformat(),
        "sweep_run_dir": str(sweep_dir),
        "mechanisms": mechanism_labels,
        "personas": persona_ids,
        "metrics": [m.name for m in metrics],
        "cells": len(cells),
        "platform": {"system": platform.system(), "python": platform.python_version()},
    }
    (out_dir / "spec09_harness_config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )

    logger.info("Spec-09 harness scoring written to {}", out_dir)
    print(str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
