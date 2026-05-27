"""End-to-end evaluation harness CLI.

Loads conversations from per-mechanism response directories, builds
the metric stack, runs the EvaluationRunner, writes the results CSVs.

Used by:

- The DoD smoke test (one mechanism x one persona x small N).
- The full Spec 09 evaluation sweep (all mechanisms x all personas).
- Reproducibility audits: same config + same seed -> identical CSV.

Usage:

    # Smoke test on the existing baseline outputs (CPU MiniCheck, no PoLL).
    uv run python scripts/run_evaluation_harness.py \\
        --persona cs_tutor \\
        --b1-dir results/run_b1 \\
        --metrics minicheck cost \\
        --out-dir results/eval_smoke

    # Full sweep with PoLL panel + SYCON.
    uv run python scripts/run_evaluation_harness.py \\
        --persona cs_tutor \\
        --b1-dir results/run_b1 --b2-dir results/run_b2 \\
        --m1-dir results/run_m1 --m3-dir results/run_m3 \\
        --metrics minicheck sycon poll cost drift_quality \\
        --out-dir results/eval_full

The metric stack is opt-in per CLI flag:

- ``minicheck`` -- MiniCheck-FT5 self-fact contradiction (default scorer
  loads on V100 / CPU fallback).
- ``sycon`` -- Worldview ToF/NoF flip rate (uses Qwen2.5-7B as judge).
- ``poll`` -- PoLL panel (Prometheus + Qwen + Llama).
- ``cost`` -- Per-mechanism cost from per-turn metadata.
- ``drift_quality`` -- M3 gate precision/recall (requires ``minicheck``
  to also be enabled -- uses the same scorer for ground-truth labels).
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.evaluation.cost import CostTracker
from persona_rag.evaluation.drift_quality import DriftQualityMetric
from persona_rag.evaluation.metrics import Metric
from persona_rag.evaluation.minicheck_metric import HFMiniCheckScorer, MiniCheckMetric
from persona_rag.evaluation.runner import EvaluationRunner, MechanismCell
from persona_rag.evaluation.sycon_metric import LlmStanceClassifier, SyconMetric
from persona_rag.evaluation.transcripts import (
    load_baseline_response_dir,
    load_m3_records_json,
)
from persona_rag.schema.persona import Persona

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWN_METRICS = ("minicheck", "sycon", "poll", "cost", "drift_quality")


def _load_persona(persona_id: str) -> Persona:
    return Persona.from_yaml(REPO_ROOT / "personas" / f"{persona_id}.yaml")


def _build_metrics(
    requested: list[str],
    *,
    minicheck_device: str,
    poll_output_dir: Path,
) -> tuple[dict[str, Metric], dict[str, Metric]]:
    """Construct metric instances. Drift-quality needs the MiniCheck scorer.

    Returns a tuple of (per_mechanism_metrics, shared_metrics):
    cost is per-mechanism (each cell needs its own ``CostTracker(mechanism=...)``);
    everything else is shared across cells.
    """
    shared: dict[str, Metric] = {}
    per_mech: dict[str, Metric] = {}
    minicheck_scorer = None

    if "minicheck" in requested or "drift_quality" in requested:
        minicheck_scorer = HFMiniCheckScorer(device=minicheck_device)
    if "minicheck" in requested:
        shared["minicheck"] = MiniCheckMetric(scorer=minicheck_scorer)
    if "sycon" in requested:
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
        shared["sycon"] = SyconMetric(classifier=LlmStanceClassifier(judge=backend))
    if "poll" in requested:
        import hashlib

        from persona_rag.evaluation.poll_panel import JudgeSpec, PoLLPanel

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

        # JSON judges cap tokens at 256 / 192: the parser pulls the
        # first balanced JSON object (~150 tokens); higher caps just
        # buy repeat-loop noise. Halves judge wall-clock on the JSON
        # judges with no parse-rate impact.
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

        class _PoLLAsTwoMetrics:
            """Per-cell PoLL panel adapter: emits both rubrics as separate metrics.

            Each (persona, conversation-set) gets its own panel run with its
            own ``output_dir`` subdir under ``poll_output_dir``. Cache key
            includes the conversation-set hash so different mechanisms with
            the same persona don't collide. The per-cell subdir also keeps
            judge checkpoints per mechanism, which is what the per-mechanism
            re-run needs (the panel's "skip if checkpoint exists" gate must
            not see a B1 checkpoint when scoring B2).
            """

            def __init__(self) -> None:
                self.name = "poll_panel"
                self._cache: dict[str, dict[str, Any]] = {}

            @staticmethod
            def _cache_key(conversations, persona) -> str:
                # Hash the conversation ids -- this is what makes the cache
                # mechanism-aware: B1 conversations and M3 conversations
                # have different ids even when persona is the same.
                ids = "|".join(c.conversation_id for c in conversations)
                conv_hash = hashlib.sha256(ids.encode("utf-8")).hexdigest()[:12]
                return f"{persona.persona_id or '<unknown>'}::{conv_hash}"

            def _run_for_cell(self, conversations, persona) -> dict[str, Any]:
                key = self._cache_key(conversations, persona)
                if key in self._cache:
                    return self._cache[key]
                # Per-cell subdir under the shared poll output root. The
                # subdir prevents the "skip if checkpoint exists" gate from
                # mistaking another mechanism's checkpoint for this cell's.
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
        shared["poll_persona_adherence"] = adapter
        shared["poll_task_quality"] = _PoLLTaskQuality(adapter)
    if "drift_quality" in requested:
        if minicheck_scorer is None:
            raise SystemExit("--metrics drift_quality requires --metrics minicheck (shares scorer)")
        shared["drift_quality"] = DriftQualityMetric(scorer=minicheck_scorer)
    if "cost" in requested:
        # CostTracker is per-mechanism; instantiated when building cells.
        per_mech["cost"] = CostTracker(mechanism="<placeholder>")
    return per_mech, shared


def _load_cells(args: argparse.Namespace) -> list[MechanismCell]:
    persona = _load_persona(args.persona)
    cells: list[MechanismCell] = []

    # Per-mechanism response-dir sources (response_*.json shape).
    for mech, src in [
        ("b1", args.b1_dir),
        ("b2", args.b2_dir),
        ("m1", args.m1_dir),
        ("m3", args.m3_dir),
    ]:
        if not src:
            continue
        convs = load_baseline_response_dir(
            Path(src).resolve(),
            mechanism=mech,
            persona_id=args.persona,
        )
        if args.max_conversations:
            convs = convs[: args.max_conversations]
        if not convs:
            logger.warning("No conversations loaded for {} from {}", mech, src)
            continue
        cells.append(
            MechanismCell(
                mechanism=mech,
                model=args.model,
                benchmark=args.benchmark,
                persona=persona,
                conversations=convs,
                seed=args.seed,
            )
        )

    # M3-style records-bundle source (run_m3_vs_baselines_pilot.py output:
    # one records.json per persona dir, keyed by pipeline name in
    # ``by_pipeline``). One ``--records-json`` flag can populate multiple
    # mechanism cells from the same file.
    if getattr(args, "records_json", None):
        records_path = Path(args.records_json).resolve()
        record_mechs = args.records_mechanisms or ["b1", "b2", "m1", "m3"]
        for mech in record_mechs:
            convs = load_m3_records_json(
                records_path,
                mechanism=mech,
                persona_id=args.persona,
            )
            if args.max_conversations:
                convs = convs[: args.max_conversations]
            if not convs:
                logger.warning(
                    "No conversations loaded for {} from records-bundle {}",
                    mech,
                    records_path,
                )
                continue
            cells.append(
                MechanismCell(
                    mechanism=mech,
                    model=args.model,
                    benchmark=args.benchmark,
                    persona=persona,
                    conversations=convs,
                    seed=args.seed,
                )
            )
    return cells


def main() -> int:
    """Run the harness end-to-end. Returns 0 on success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona", required=True)
    parser.add_argument("--b1-dir", help="dir of response_*.json for B1")
    parser.add_argument("--b2-dir", help="dir of response_*.json for B2")
    parser.add_argument("--m1-dir", help="dir of response_*.json for M1")
    parser.add_argument("--m3-dir", help="dir of response_*.json for M3")
    parser.add_argument(
        "--records-json",
        help=(
            "path to a run_m3_vs_baselines_pilot.py records.json bundle. "
            "Pulls one cell per mechanism present in the file (filter via "
            "--records-mechanisms)."
        ),
    )
    parser.add_argument(
        "--records-mechanisms",
        nargs="+",
        choices=["b1", "b2", "m1", "m3"],
        help="which mechanisms to extract from --records-json (default: all four)",
    )
    parser.add_argument(
        "--metrics",
        nargs="+",
        choices=KNOWN_METRICS,
        default=["minicheck", "cost"],
        help="metrics to enable; default: minicheck + cost",
    )
    parser.add_argument(
        "--minicheck-device",
        default="cpu",
        help='"cpu" (default; works on Mac dev) or "cuda" for V100',
    )
    parser.add_argument("--max-conversations", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default="gemma2-9b-it")
    parser.add_argument("--benchmark", default="single_turn_pilot")
    parser.add_argument(
        "--out-dir",
        default="results/eval",
        help="output root; a timestamped subdir is created",
    )
    parser.add_argument("--wandb", action="store_true")
    parser.add_argument("--wandb-mode", default="offline")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = REPO_ROOT / args.out_dir / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    cells = _load_cells(args)
    if not cells:
        raise SystemExit(
            "No cells loaded -- pass at least one of --b1-dir / --b2-dir / --m1-dir / --m3-dir"
        )

    per_mech_metrics, shared_metrics = _build_metrics(
        args.metrics,
        minicheck_device=args.minicheck_device,
        poll_output_dir=out_dir / "poll",
    )

    # Build the final metric list: shared metrics + a CostTracker per mechanism if cost is enabled.
    all_metrics: list[Metric] = list(shared_metrics.values())
    if "cost" in per_mech_metrics:
        for cell in cells:
            all_metrics.append(CostTracker(mechanism=cell.mechanism))

    runner = EvaluationRunner(
        output_dir=out_dir,
        metrics=all_metrics,
        seed=args.seed,
        wandb_enabled=args.wandb,
        wandb_mode=args.wandb_mode,
    )
    runner.run(cells)
    logger.info("Eval done. results at {}", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
