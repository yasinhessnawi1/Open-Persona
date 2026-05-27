"""Full Spec-09 sweep: B1 / B2 / M1 / M3 over the 90-conversation probe suite.

Replays every counterfactual-probe conversation through each mechanism via
``ProbeRunner``, writing per-mechanism transcripts in EvalConversation
shape. Output layout::

    <out_root>/<run_id>/
      run_config.json
      injection_logs/<mechanism>.json
      transcripts/<mechanism>/<conversation_id>.json
      report.md

The transcripts are scored downstream by ``scripts/run_spec09_harness.py``
(separate driver) which loads them as ``EvalConversation`` objects through
the Spec-8 ``EvaluationRunner`` + the full metric stack (MiniCheck, SYCON,
PoLL panel, CostTracker, DriftQualityMetric for M3).

Decoupling sweep from scoring keeps wall-clock predictable: the sweep is
the long pole (~20-30h for 90 x 4 mechanisms x 7 turns on V100); the
harness scoring is fast in comparison and benefits from being re-runnable
without regenerating the transcripts.

V100-only: loads Gemma-2-9B (responder for B1/B2/M1/M3) + Qwen2.5-7B
(M3 gate) + Prometheus-2-7B (M3 rerank judge).

Usage::

    uv run python scripts/run_spec09_full_sweep.py \\
        --probes-root benchmarks_data/counterfactual_probes \\
        --personas cs_tutor,historian,climate_scientist \\
        --mechanisms B1,B2,M1,M3 \\
        --seed 42 \\
        --out-root results/spec09_full_sweep
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.benchmarks import (
    load_counterfactual_probe_suite,
)
from persona_rag.evaluation.probe_runner import ProbeInjectionLog, ProbeRunner
from persona_rag.models import (
    GemmaBackend,
    HFBackendConfig,
    PrometheusBackend,
    QwenBackend,
)
from persona_rag.retrieval import (
    CharacterRMScorer,
    DriftGatedMechanism,
    FewShotBundle,
    HybridRanker,
    LlmJudgeDriftGate,
    PromptPersonaRAG,
    TypedRetrievalRAG,
    VanillaRAG,
)
from persona_rag.schema.chunker import chunk_persona
from persona_rag.schema.persona import Persona
from persona_rag.stores import (
    EpisodicStore,
    IdentityStore,
    SelfFactsStore,
    WorldviewStore,
)
from persona_rag.stores.knowledge_store import KnowledgeDocument, KnowledgeStore

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROBES_ROOT = REPO_ROOT / "benchmarks_data" / "counterfactual_probes"
DEFAULT_PERSONAS = ("cs_tutor", "historian", "climate_scientist")
DEFAULT_MECHANISMS = ("B1", "B2", "M1", "M3")


def _build_responder() -> Any:
    cfg = HFBackendConfig(
        model_id="google/gemma-2-9b-it",
        name="gemma2-9b-it",
        revision=None,
        compute_dtype="float16",
        attn_implementation="eager",
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        max_input_tokens=3500,
        trust_remote_code=False,
        warmup_nan_guard=True,
    )
    return GemmaBackend(cfg)


def _build_gate_judge() -> Any:
    """Qwen2.5-7B-Instruct as the M3 gate-judge (decision #054 verdict)."""
    return QwenBackend(
        QwenBackend.default_config(
            model_id="Qwen/Qwen2.5-7B-Instruct",
            name="qwen2.5-7b-instruct",
            revision=None,
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            max_input_tokens=3500,
            trust_remote_code=False,
            warmup_nan_guard=True,
        )
    )


def _build_rerank_judge() -> Any:
    """Prometheus-2-7B as the M3 rerank judge (cross-family from gate)."""
    return PrometheusBackend(
        PrometheusBackend.default_config(
            model_id="prometheus-eval/prometheus-7b-v2.0",
            name="prometheus-2-7b",
            revision=None,
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            max_input_tokens=3500,
            trust_remote_code=False,
            warmup_nan_guard=True,
        )
    )


def _load_persona_pipelines(
    *,
    persona_id: str,
    responder: Any,
    gate_judge: Any | None,
    rerank_judge: Any | None,
) -> tuple[Persona, dict[str, Any], KnowledgeStore]:
    """Build all four mechanisms for one persona; returns (persona, pipelines, store)."""
    persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{persona_id}.yaml")

    knowledge_store = KnowledgeStore(
        persist_path=REPO_ROOT / ".chroma" / f"knowledge_spec09_full_{persona_id}",
    )
    corpus_path = REPO_ROOT / "benchmarks_data" / "knowledge_corpora" / persona_id
    if corpus_path.exists():
        knowledge_store.index_corpus(
            [
                KnowledgeDocument(
                    doc_id=md.stem,
                    text=md.read_text(encoding="utf-8"),
                    source=md.name,
                    metadata={"corpus": persona_id},
                )
                for md in sorted(corpus_path.glob("*.md"))
            ]
        )
    else:
        logger.warning("no knowledge corpus for {} - running with empty store", persona_id)

    persist = REPO_ROOT / ".chroma" / f"persona_spec09_full_{persona_id}"
    typed_stores = (
        IdentityStore(persist),
        SelfFactsStore(persist),
        WorldviewStore(persist),
        EpisodicStore(persist),
    )
    chunks = chunk_persona(persona)
    for store in typed_stores:
        store.index(chunks)

    pipelines: dict[str, Any] = {}

    pipelines["B1"] = VanillaRAG(backend=responder, knowledge_store=knowledge_store)

    few_shots_path = REPO_ROOT / "personas" / "examples" / f"{persona_id}.yaml"
    if few_shots_path.exists():
        pipelines["B2"] = PromptPersonaRAG(
            backend=responder,
            knowledge_store=knowledge_store,
            few_shots=FewShotBundle.from_yaml(few_shots_path),
            max_input_tokens=3500,
        )
    else:
        logger.warning("no few-shots for {} -- B2 unavailable", persona_id)

    m1 = TypedRetrievalRAG(
        backend=responder,
        knowledge_store=knowledge_store,
        identity_store=typed_stores[0],
        self_facts_store=typed_stores[1],
        worldview_store=typed_stores[2],
        episodic_store=typed_stores[3],
    )
    pipelines["M1"] = m1

    if gate_judge is not None and rerank_judge is not None:
        gate = LlmJudgeDriftGate(judge=gate_judge, confidence_threshold=0.5)
        ranker = HybridRanker(
            character_rm=CharacterRMScorer(),
            rerank_judge=rerank_judge,
            enabled_signals=("judge",),
        )
        pipelines["M3"] = DriftGatedMechanism(
            backend=responder, m1=m1, drift_gate=gate, hybrid_ranker=ranker
        )

    return persona, pipelines, knowledge_store


def _git_rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, encoding="utf-8"
        ).strip()
    except Exception:
        return "unknown"


def _write_transcript(out_dir: Path, transcript: Any) -> Path:
    payload = {
        "conversation_id": transcript.conversation_id,
        "mechanism": transcript.mechanism,
        "persona_id": transcript.persona_id,
        "turns": [
            {
                "turn_index": t.turn_index,
                "user_text": t.user_text,
                "assistant_text": t.assistant_text,
            }
            for t in transcript.turns
        ],
        "per_turn_metadata": list(transcript.per_turn_metadata),
    }
    safe_id = transcript.conversation_id.replace("::", "__").replace("/", "_")
    path = out_dir / f"{safe_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--probes-root", type=Path, default=DEFAULT_PROBES_ROOT)
    parser.add_argument("--personas", default=",".join(DEFAULT_PERSONAS))
    parser.add_argument(
        "--mechanisms",
        default=",".join(DEFAULT_MECHANISMS),
        help="Comma-separated subset of {B1, B2, M1, M3}.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out-root", type=Path, default=REPO_ROOT / "results" / "spec09_full_sweep"
    )
    parser.add_argument("--run-id", type=str, default=None)
    args = parser.parse_args()

    persona_ids = [p.strip() for p in args.personas.split(",") if p.strip()]
    mechanism_labels = [m.strip() for m in args.mechanisms.split(",") if m.strip()]
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir: Path = args.out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info("loading counterfactual-probe suite from {}", args.probes_root)
    all_conversations, chunk_index = load_counterfactual_probe_suite(args.probes_root)
    conversations = [c for c in all_conversations if c.persona_id in persona_ids]

    logger.info(
        "Spec-09 full sweep: {} conversations x {} mechanisms across {} personas",
        len(conversations),
        len(mechanism_labels),
        len(persona_ids),
    )

    # Build the long-running models once.
    responder = _build_responder()
    needs_m3 = "M3" in mechanism_labels
    gate_judge = _build_gate_judge() if needs_m3 else None
    rerank_judge = _build_rerank_judge() if needs_m3 else None

    transcripts_root = run_dir / "transcripts"
    injection_logs_root = run_dir / "injection_logs"
    transcripts_root.mkdir(parents=True, exist_ok=True)
    injection_logs_root.mkdir(parents=True, exist_ok=True)

    # Run each mechanism over every persona's conversations.
    by_mechanism_logs: dict[str, list[ProbeInjectionLog]] = {m: [] for m in mechanism_labels}
    for persona_id in persona_ids:
        persona, pipelines, knowledge_store = _load_persona_pipelines(
            persona_id=persona_id,
            responder=responder,
            gate_judge=gate_judge,
            rerank_judge=rerank_judge,
        )
        per_persona_convs = [c for c in conversations if c.persona_id == persona_id]
        per_persona_chunks = {
            cid: ch for cid, ch in chunk_index.items() if ch.persona_id == persona_id
        }
        for mechanism_label in mechanism_labels:
            pipeline = pipelines.get(mechanism_label)
            if pipeline is None:
                logger.warning(
                    "skipping {} for {} -- pipeline unavailable", mechanism_label, persona_id
                )
                continue
            logger.info(
                "running {} over {} probes for persona {}",
                mechanism_label,
                len(per_persona_convs),
                persona_id,
            )
            runner = ProbeRunner(
                pipeline=pipeline,
                knowledge_store=knowledge_store,
                chunks=per_persona_chunks,
                seed=args.seed,
                mechanism_label=mechanism_label,
            )
            mech_dir = transcripts_root / mechanism_label
            mech_dir.mkdir(parents=True, exist_ok=True)
            transcripts, injection_logs = runner.replay(persona, per_persona_convs)
            for transcript in transcripts:
                _write_transcript(mech_dir, transcript)
            by_mechanism_logs[mechanism_label].extend(injection_logs)

    # Persist per-mechanism injection logs.
    for mech, logs in by_mechanism_logs.items():
        out_path = injection_logs_root / f"{mech}.json"
        out_path.write_text(
            json.dumps([asdict(log) for log in logs], indent=2) + "\n", encoding="utf-8"
        )

    # Run config + report.
    run_config = {
        "run_id": run_id,
        "git_rev": _git_rev(),
        "timestamp": datetime.now().isoformat(),
        "mechanisms": mechanism_labels,
        "personas": persona_ids,
        "seed": args.seed,
        "n_conversations_per_persona": len(conversations) // max(1, len(persona_ids)),
        "n_total_conversations": len(conversations),
        "n_chunks_loaded": len(chunk_index),
        "n_mechanism_runs": sum(len(logs) for logs in by_mechanism_logs.values()),
        "platform": {"system": platform.system(), "python": platform.python_version()},
    }
    (run_dir / "run_config.json").write_text(
        json.dumps(run_config, indent=2) + "\n", encoding="utf-8"
    )

    report_lines = [
        f"# Spec-09 full sweep -- run {run_id}",
        "",
        f"- mechanisms: {mechanism_labels}",
        f"- personas: {persona_ids}",
        f"- conversations: {len(conversations)}",
        "",
        "## Counterfactual injection summary",
        "",
        "| mechanism | n_logs | in_topk | mean_rank |",
        "|---|---|---|---|",
    ]
    for mech, logs in by_mechanism_logs.items():
        in_topk = sum(1 for log in logs if log.injected_chunk_in_topk)
        ranks = [log.injected_chunk_rank for log in logs if log.injected_chunk_rank is not None]
        mean_rank = round(sum(ranks) / len(ranks), 2) if ranks else None
        report_lines.append(f"| {mech} | {len(logs)} | {in_topk} | {mean_rank} |")
    report_lines += [
        "",
        "## Next step",
        "",
        "Run `scripts/run_spec09_harness.py --run-dir " + str(run_dir) + "` to score the",
        "transcripts through the Spec-8 metric stack.",
        "",
    ]
    (run_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    logger.info("Spec-09 full-sweep transcripts written to {}", run_dir)
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
