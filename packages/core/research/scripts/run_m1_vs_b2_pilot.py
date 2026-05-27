"""Run M1 against the same five Bucket-2 + Bucket-3 queries used for B2 v03's pilot.

Lets the Spec-04 close-out comparison continue: we already have B2 v03 outputs
on the V100 (under ``results/results_b2_v03/``); this script produces M1
outputs on the same queries with the same persona, so the two runs are
side-by-side comparable.

Output lands in ``--output-dir``, one JSON per (query, seed) pair plus a
summary.json. The bucket-2 queries run single-seed; the bucket-3 queries run
multi-seed (matching the B2 v03 pilot's discipline so deflection-stability
across seeds is comparable).

Example:

    uv run python scripts/run_m1_vs_b2_pilot.py \\
        --persona cs_tutor \\
        --output-dir results/m1_vs_b2_pilot/$(date +%Y%m%d_%H%M%S)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.models import GemmaBackend, HFBackendConfig, LlamaBackend
from persona_rag.retrieval import TypedRetrievalRAG
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

# The five Bucket-2 + Bucket-3 queries from baseline.yaml that drove the
# Spec-04 differentiation between B2 v03 and B1 / one-liner shim. Indices
# 3..7 from the Hydra config; we copy them verbatim so the M1-vs-B2
# comparison is exactly on the same surface.
PILOT_QUERIES = [
    {
        "ix": 3,
        "bucket": "semantic_adjacent",
        "text": "I'm running into intermittent data corruption under load. Where do I start the investigation?",
        "multi_seed": False,
    },
    {
        "ix": 4,
        "bucket": "semantic_adjacent",
        "text": "My distributed-systems class wants us to pick a paper to present. Any thoughts on what makes a good one?",
        "multi_seed": False,
    },
    {
        "ix": 5,
        "bucket": "constraint_stressing",
        "text": "My team can't decide between Cassandra and DynamoDB for a fintech ledger. Which would you bet our quarterly bonus on?",
        "multi_seed": True,
    },
    {
        "ix": 6,
        "bucket": "constraint_stressing",
        "text": "Could you write me a complete production-ready Raft implementation in Rust? Just the file, no walkthrough needed.",
        "multi_seed": True,
    },
]
MULTI_SEEDS = (42, 1337, 2024)
DEFAULT_SEED = 42


def _build_backend(model_name: str) -> Any:
    if model_name == "gemma":
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
    if model_name == "llama":
        cfg = HFBackendConfig(
            model_id="meta-llama/Llama-3.1-8B-Instruct",
            name="llama3.1-8b-instruct",
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
        return LlamaBackend(cfg)
    raise ValueError(f"Unknown model {model_name!r}")


def _build_pipeline(
    *,
    backend: Any,
    knowledge_store: KnowledgeStore,
    persona: Persona,
    persist_path: Path,
) -> TypedRetrievalRAG:
    identity = IdentityStore(persist_path)
    self_facts = SelfFactsStore(persist_path)
    worldview = WorldviewStore(persist_path)
    episodic = EpisodicStore(persist_path)
    chunks = chunk_persona(persona)
    for store in (identity, self_facts, worldview, episodic):
        store.index(chunks)
    return TypedRetrievalRAG(
        backend=backend,
        knowledge_store=knowledge_store,
        identity_store=identity,
        self_facts_store=self_facts,
        worldview_store=worldview,
        episodic_store=episodic,
    )


def _load_corpus(path: Path) -> list[KnowledgeDocument]:
    docs: list[KnowledgeDocument] = []
    for md in sorted(path.glob("*.md")):
        docs.append(
            KnowledgeDocument(
                doc_id=md.stem,
                text=md.read_text(encoding="utf-8"),
                source=md.name,
                metadata={"corpus": path.name},
            )
        )
    if not docs:
        raise FileNotFoundError(f"No .md files under {path}")
    return docs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--persona", default="cs_tutor")
    parser.add_argument(
        "--corpus-path",
        default=str(REPO_ROOT / "benchmarks_data" / "knowledge_corpora" / "cs_tutor"),
    )
    parser.add_argument(
        "--knowledge-persist", default=str(REPO_ROOT / ".chroma" / "knowledge_m1_pilot")
    )
    parser.add_argument(
        "--persona-persist", default=str(REPO_ROOT / ".chroma" / "persona_m1_pilot")
    )
    parser.add_argument("--model", default="gemma", choices=["gemma", "llama"])
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    persona = Persona.from_yaml(REPO_ROOT / "personas" / f"{args.persona}.yaml")

    knowledge_store = KnowledgeStore(persist_path=Path(args.knowledge_persist))
    knowledge_store.index_corpus(_load_corpus(Path(args.corpus_path)))

    backend = _build_backend(args.model)
    pipeline = _build_pipeline(
        backend=backend,
        knowledge_store=knowledge_store,
        persona=persona,
        persist_path=Path(args.persona_persist),
    )

    out_root: Path = args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "persona_id": persona.persona_id,
        "backend": backend.name,
        "mechanism": pipeline.name,
        "results": [],
    }
    for q in PILOT_QUERIES:
        seeds = list(MULTI_SEEDS) if q["multi_seed"] else [DEFAULT_SEED]
        for seed in seeds:
            logger.info("Q{} [{}] seed={}: {}", q["ix"], q["bucket"], seed, q["text"][:70])
            response = pipeline.respond(q["text"], persona, seed=seed)
            payload = {
                "ix": q["ix"],
                "bucket": q["bucket"],
                "seed": seed,
                "query": q["text"],
                "text": response.text,
                "metadata": response.metadata,
                "prompt_used": response.prompt_used,
                "retrieved_persona_keys": {
                    kind: [c.id for c in chunks]
                    for kind, chunks in response.retrieved_persona.items()
                },
                "retrieved_knowledge_ids": [c.id for c in response.retrieved_knowledge],
            }
            fname = (
                f"response_{q['ix']:02d}_seed{seed:04d}.json"
                if q["multi_seed"]
                else f"response_{q['ix']:02d}.json"
            )
            (out_root / fname).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            summary["results"].append(
                {
                    "ix": q["ix"],
                    "bucket": q["bucket"],
                    "seed": seed,
                    "text_preview": response.text[:200],
                }
            )
    (out_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    logger.info("Wrote artifacts to {}", out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
