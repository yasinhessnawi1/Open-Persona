"""Side-by-side comparison of B1 / B2 / M1 / M3 on a 5-query x 3-persona bench.

The query bench combines two known drift-triggering queries per persona
drawn from the existing drift-trajectory corpus (the user turns where the
authored drift-level is ``clear`` or ``break``) with one bucket-2 query
per persona from the pilot ``baseline.yaml``. Each pipeline runs over
the bench; per-query artefacts and a side-by-side Markdown summary are
written.

Usage:
    uv run python scripts/run_m3_vs_baselines_pilot.py \\
        --output-dir results/m3_vs_baselines_pilot/$(date +%Y%m%d_%H%M%S)

V100-only: loads Gemma-2-9B (responder) + Llama-3.1-8B (gate / rerank
judge) + the reward model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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
    FewShotBundle,
    HybridRanker,
    LlmJudgeDriftGate,
    PromptPersonaRAG,
    TypedRetrievalRAG,
    VanillaRAG,
)
from persona_rag.retrieval.base import Turn
from persona_rag.schema.chunker import chunk_persona
from persona_rag.schema.conversation import DriftTrajectoryConversation
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

    SDPA attention via QwenBackend.default_config — eager + fp16 + 4-bit
    triggers NaN logits on V100 for Qwen's bf16-trained weights.
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

    The rerank prompt is the rubric-format `[RESULT] X` shape Prometheus
    is trained on, so the format-mismatch failure mode the gate sweep
    surfaced does not apply here.
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


def _bench_queries(persona_id: str) -> list[dict[str, Any]]:
    """Pick 5 queries for one persona, each carrying its conversation history.

    Two come from the drifting drift-trajectory conversation (annotated
    ``clear`` or ``break`` drift_level — the strongest stress points). For
    those, the history is threaded up to (but not including) the chosen
    user turn — so the gate sees the *prior* assistant turn from the
    drifting trajectory and can evaluate whether that prior turn drifted.
    One comes from the corresponding in_persona conversation as a control
    (turn 0; empty history). Up to two more in_persona user turns fill
    the remaining slots, each with the preceding in_persona history
    threaded so the comparison conditions are consistent across pipelines.
    """
    base = REPO_ROOT / "benchmarks_data" / "drift_trajectory" / persona_id
    drifting = DriftTrajectoryConversation.from_yaml(base / "drifting.yaml")
    in_persona = DriftTrajectoryConversation.from_yaml(base / "in_persona.yaml")
    drift_indices = [
        i
        for i, t in enumerate(drifting.turns)
        if t.role == "assistant" and t.drift_level in ("clear", "break")
    ][:2]
    out: list[dict[str, Any]] = []
    for ix in drift_indices:
        # User turn at index ix-1 prompted the drifting assistant turn ix.
        # Thread turns 0 ... ix-2 as conversation history so the gate sees
        # the prior assistant turn from the drifting trajectory.
        user_pos = ix - 1
        if 0 <= user_pos < len(drifting.turns):
            history = [Turn(role=t.role, content=t.text) for t in drifting.turns[:user_pos]]
            out.append(
                {
                    "id": f"{persona_id}_drift_t{ix // 2}",
                    "text": drifting.turns[user_pos].text,
                    "label": "drift_triggering",
                    "history": history,
                }
            )
    # Control: in_persona turn 0 with empty history.
    in_persona_user_texts = in_persona.user_turn_texts()
    if in_persona_user_texts:
        out.append(
            {
                "id": f"{persona_id}_control",
                "text": in_persona_user_texts[0],
                "label": "in_persona_control",
                "history": [],
            }
        )
    # Pad to 5 with extra in_persona user turns; each carries the
    # preceding in_persona history so the gate has something to evaluate.
    extra_count = 0
    for ix, t in enumerate(in_persona.turns):
        if len(out) >= 5:
            break
        if t.role != "user" or ix == 0:
            continue
        history = [Turn(role=tt.role, content=tt.text) for tt in in_persona.turns[:ix]]
        out.append(
            {
                "id": f"{persona_id}_extra_{extra_count}",
                "text": t.text,
                "label": "in_persona_extra",
                "history": history,
            }
        )
        extra_count += 1
    return out[:5]


def main() -> int:
    """Integration-check entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--personas",
        default="cs_tutor,historian,climate_scientist",
        help="Comma-separated persona ids.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    persona_ids = [p.strip() for p in args.personas.split(",") if p.strip()]
    out_root: Path = args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    responder = _build_responder()
    gate_judge = _build_gate_judge()
    rerank_judge = _build_rerank_judge()

    summary: dict[str, Any] = {"per_persona": {}}
    for pid in persona_ids:
        persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{pid}.yaml")
        bench = _bench_queries(pid)

        knowledge_store = KnowledgeStore(
            persist_path=REPO_ROOT / ".chroma" / f"knowledge_m3_pilot_{pid}"
        )
        corpus_path = REPO_ROOT / "benchmarks_data" / "knowledge_corpora" / pid
        if corpus_path.exists():
            knowledge_store.index_corpus(
                [
                    KnowledgeDocument(
                        doc_id=md.stem,
                        text=md.read_text(encoding="utf-8"),
                        source=md.name,
                        metadata={"corpus": pid},
                    )
                    for md in sorted(corpus_path.glob("*.md"))
                ]
            )
        else:
            logger.warning("no knowledge corpus for {} — running with empty store", pid)

        persist = REPO_ROOT / ".chroma" / f"persona_m3_pilot_{pid}"
        typed_stores = (
            IdentityStore(persist),
            SelfFactsStore(persist),
            WorldviewStore(persist),
            EpisodicStore(persist),
        )
        chunks = chunk_persona(persona)
        for store in typed_stores:
            store.index(chunks)

        b1 = VanillaRAG(backend=responder, knowledge_store=knowledge_store)
        few_shots_path = REPO_ROOT / "personas" / "examples" / f"{pid}.yaml"
        b2: PromptPersonaRAG | None = None
        if few_shots_path.exists():
            b2 = PromptPersonaRAG(
                backend=responder,
                knowledge_store=knowledge_store,
                few_shots=FewShotBundle.from_yaml(few_shots_path),
            )
        m1 = TypedRetrievalRAG(
            backend=responder,
            knowledge_store=knowledge_store,
            identity_store=typed_stores[0],
            self_facts_store=typed_stores[1],
            worldview_store=typed_stores[2],
            episodic_store=typed_stores[3],
        )
        gate = LlmJudgeDriftGate(judge=gate_judge, confidence_threshold=0.5)
        # CharacterRM disabled (custom-class loader unavailable on HF +
        # V100 memory budget). 1-signal judge-only re-rank.
        ranker = HybridRanker(
            character_rm=CharacterRMScorer(),
            rerank_judge=rerank_judge,
            enabled_signals=("judge",),
        )
        m3 = DriftGatedMechanism(backend=responder, m1=m1, drift_gate=gate, hybrid_ranker=ranker)

        records: list[dict[str, Any]] = []
        for q in bench:
            history: list[Turn] = list(q.get("history") or [])
            text = q["text"]
            entry: dict[str, Any] = {
                "query_id": q["id"],
                "query": text,
                "label": q["label"],
                "by_pipeline": {},
            }
            for label, pipeline in [
                ("B1", b1),
                ("B2", b2),
                ("M1", m1),
                ("M3", m3),
            ]:
                if pipeline is None:
                    continue
                response = pipeline.respond(text, persona, history=history, seed=args.seed)
                entry["by_pipeline"][label] = {
                    "text": response.text,
                    "metadata": response.metadata,
                }
            records.append(entry)
        per_dir = out_root / pid
        per_dir.mkdir(parents=True, exist_ok=True)
        (per_dir / "records.json").write_text(
            json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        # Side-by-side Markdown.
        md = ["# B1 / B2 / M1 / M3 side-by-side", "", f"## persona = {pid}", ""]
        for r in records:
            md.append(f"### {r['query_id']} ({r['label']})")
            md.append("")
            md.append(f"**Query:** {r['query']}")
            md.append("")
            for label, pl in r["by_pipeline"].items():
                md.append(f"**{label}:** {pl['text'][:300].strip()}...")
                md.append("")
        (per_dir / "side_by_side.md").write_text("\n".join(md), encoding="utf-8")
        summary["per_persona"][pid] = {"n_queries": len(records)}

    (out_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    logger.info("integration-check artefacts written to {}", out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
