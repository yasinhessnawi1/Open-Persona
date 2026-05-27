"""Drift-gate threshold sensitivity sweep on the existing pilot conversations.

Replays the drift-gated mechanism over the pilot bucket-2 + bucket-3
queries from ``baseline.yaml`` at a range of gate thresholds. For each
threshold, the runner records (a) the gate-trigger rate (fraction of
turns that took the gated path) and (b) per-turn metadata so an
identity-consistency curve can be plotted offline.

Usage:
    uv run python scripts/run_m3_threshold_sweep.py \\
        --persona cs_tutor \\
        --thresholds 0.3,0.4,0.5,0.6,0.7 \\
        --output-dir results/m3_threshold_sweep/$(date +%Y%m%d_%H%M%S)

V100-only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from persona_rag.models import (
    GemmaBackend,
    HFBackendConfig,
    PrometheusBackend,
    QwenBackend,
)
from persona_rag.retrieval import (
    CharacterRMScorer,
    DriftGatedMechanism,
    HybridRanker,
    LlmJudgeDriftGate,
    TypedRetrievalRAG,
)
from persona_rag.retrieval.base import Turn
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
        max_input_tokens=4096,
        trust_remote_code=False,
        warmup_nan_guard=True,
    )
    return GemmaBackend(cfg)


def _build_gate_judge() -> Any:
    """Qwen2.5-7B-Instruct as the gate-judge (per the cross-tier sweep verdict).

    SDPA attention is required on V100 fp16 + 4-bit; QwenBackend's
    ``default_config`` carries that override.
    """
    return QwenBackend(
        QwenBackend.default_config(
            model_id="Qwen/Qwen2.5-7B-Instruct",
            name="qwen2.5-7b-instruct",
            revision=None,
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            max_input_tokens=4096,
            trust_remote_code=False,
            warmup_nan_guard=True,
        )
    )


def _build_rerank_judge() -> Any:
    """Prometheus-2-7B as the rerank-judge (cross-family from the gate-judge).

    Used inside the hybrid ranker. The rerank prompt is the rubric-format
    `[RESULT] X` shape Prometheus is trained on, so the format-mismatch
    failure mode the gate sweep surfaced does not apply here.
    """
    return PrometheusBackend(
        PrometheusBackend.default_config(
            model_id="prometheus-eval/prometheus-7b-v2.0",
            name="prometheus-2-7b",
            revision=None,
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            max_input_tokens=4096,
            trust_remote_code=False,
            warmup_nan_guard=True,
        )
    )


def _load_pilot_queries(baseline_yaml: Path) -> list[dict[str, Any]]:
    """Load queries with ``bucket in {semantic_adjacent, constraint_stressing}``."""
    raw = yaml.safe_load(baseline_yaml.read_text(encoding="utf-8"))
    queries = []
    for q in raw["test_queries"]:
        if q["bucket"] in ("semantic_adjacent", "constraint_stressing"):
            queries.append(q)
    return queries


def main() -> int:
    """Threshold sensitivity sweep entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--persona", default="cs_tutor")
    parser.add_argument("--thresholds", default="0.3,0.4,0.5,0.6,0.7")
    parser.add_argument(
        "--baseline-yaml",
        default=str(REPO_ROOT / "src" / "persona_rag" / "config" / "baseline.yaml"),
    )
    parser.add_argument(
        "--corpus-path",
        default=str(REPO_ROOT / "benchmarks_data" / "knowledge_corpora" / "cs_tutor"),
    )
    parser.add_argument(
        "--knowledge-persist", default=str(REPO_ROOT / ".chroma" / "knowledge_m3_sweep")
    )
    parser.add_argument(
        "--persona-persist", default=str(REPO_ROOT / ".chroma" / "persona_m3_sweep")
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    thresholds = [float(t) for t in args.thresholds.split(",")]
    persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{args.persona}.yaml")
    queries = _load_pilot_queries(Path(args.baseline_yaml))
    logger.info("loaded {} pilot queries", len(queries))

    out_root: Path = args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    knowledge_store = KnowledgeStore(persist_path=Path(args.knowledge_persist))
    knowledge_store.index_corpus(
        [
            KnowledgeDocument(
                doc_id=md.stem,
                text=md.read_text(encoding="utf-8"),
                source=md.name,
                metadata={"corpus": Path(args.corpus_path).name},
            )
            for md in sorted(Path(args.corpus_path).glob("*.md"))
        ]
    )

    persist = Path(args.persona_persist)
    typed_stores = (
        IdentityStore(persist),
        SelfFactsStore(persist),
        WorldviewStore(persist),
        EpisodicStore(persist),
    )
    chunks = chunk_persona(persona)
    for store in typed_stores:
        store.index(chunks)

    responder = _build_responder()
    gate_judge = _build_gate_judge()
    rerank_judge = _build_rerank_judge()

    m1 = TypedRetrievalRAG(
        backend=responder,
        knowledge_store=knowledge_store,
        identity_store=typed_stores[0],
        self_facts_store=typed_stores[1],
        worldview_store=typed_stores[2],
        episodic_store=typed_stores[3],
    )
    # CharacterRM disabled (custom-class loader unavailable on HF + V100
    # memory budget). Ranker degenerates to 1-signal LLM-judge re-rank
    # via ``enabled_signals=("judge",)``; ``character_rm`` field still
    # required by the dataclass but its ``score()`` is never invoked.
    char_rm = CharacterRMScorer()
    ranker = HybridRanker(
        character_rm=char_rm,
        rerank_judge=rerank_judge,
        enabled_signals=("judge",),
    )

    summary: dict[str, Any] = {"persona_id": persona.persona_id, "thresholds": {}}
    for t in thresholds:
        gate = LlmJudgeDriftGate(judge=gate_judge, confidence_threshold=t)
        m3 = DriftGatedMechanism(backend=responder, m1=m1, drift_gate=gate, hybrid_ranker=ranker)
        per_query: list[dict[str, Any]] = []
        gated_count = 0
        # Build a tiny one-pair history so the gate has something to evaluate.
        # The pilot queries are single-turn from the user's POV; we synthesise
        # a neutral previous turn so the gate is reachable.
        history = [
            Turn(role="user", content="Earlier question."),
            Turn(role="assistant", content="Earlier neutral reply."),
        ]
        for q in queries:
            response = m3.respond(q["text"], persona, history=history, seed=args.seed)
            md = response.metadata
            if md.get("path_taken") == "gated":
                gated_count += 1
            per_query.append(
                {
                    "query": q["text"],
                    "bucket": q["bucket"],
                    "path_taken": md.get("path_taken"),
                    "gate_flag": md.get("gate_flag"),
                    "gate_confidence": md.get("gate_confidence"),
                    "llm_call_count": md.get("llm_call_count"),
                    "text_preview": response.text[:200],
                }
            )
        n = len(queries)
        summary["thresholds"][f"{t}"] = {
            "n_queries": n,
            "gate_trigger_rate": gated_count / n if n else 0.0,
            "queries": per_query,
        }
        logger.info(
            "threshold={:.2f} gated={}/{} ({:.0%})",
            t,
            gated_count,
            n,
            gated_count / n if n else 0.0,
        )

    (out_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    logger.info("threshold-sweep artefacts written to {}", out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
