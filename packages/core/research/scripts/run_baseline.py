"""Run a baseline retrieval pipeline (vanilla RAG or prompt-persona) end-to-end.

Hydra entry point composes ``model + knowledge_store + retrieval`` from
``src/persona_rag/config/``. Loads the model, indexes the knowledge corpus
(idempotent — re-runs are cheap), runs each test query through the configured
pipeline, and writes per-query response artefacts + a summary JSON.

Examples:
    uv run python scripts/run_baseline.py
    uv run python scripts/run_baseline.py retrieval=prompt_persona
    uv run python scripts/run_baseline.py model=llama retrieval=prompt_persona
    uv run python scripts/run_baseline.py \\
        retrieval=prompt_persona retrieval.b2_variant=v02_one_liner
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hydra
from loguru import logger
from omegaconf import DictConfig, OmegaConf

from persona_rag.retrieval import (
    CharacterRMScorer,
    DriftGatedMechanism,
    FewShotBundle,
    HybridRanker,
    LlmJudgeDriftGate,
    PromptPersonaRAG,
    Response,
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


def _resolve(p: str | Path) -> Path:
    """Make config-relative paths absolute against the repo root."""
    pp = Path(p)
    return pp if pp.is_absolute() else (REPO_ROOT / pp).resolve()


def _load_corpus(corpus_path: Path) -> list[KnowledgeDocument]:
    """Load every .md file under `corpus_path` into a `KnowledgeDocument`."""
    if not corpus_path.exists():
        raise FileNotFoundError(
            f"Knowledge corpus not found at {corpus_path}. "
            "Default lives at benchmarks_data/knowledge_corpora/cs_tutor/."
        )
    docs: list[KnowledgeDocument] = []
    for md_path in sorted(corpus_path.glob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        docs.append(
            KnowledgeDocument(
                doc_id=md_path.stem,
                text=text,
                source=md_path.name,
                metadata={"corpus": corpus_path.name},
            )
        )
    if not docs:
        raise FileNotFoundError(f"No .md files under {corpus_path}")
    logger.info("Loaded {} document(s) from {}", len(docs), corpus_path)
    return docs


def _build_backend(model_cfg: DictConfig) -> Any:
    """Construct a Gemma or Llama LLMBackend from the Hydra model group.

    Mirrors ``scripts/smoke_test_models.py::_build_backend`` so both scripts
    share one pattern for backend construction.
    """
    from persona_rag.models import GemmaBackend, HFBackendConfig, LlamaBackend

    cfg = HFBackendConfig(
        model_id=model_cfg.model_id,
        name=model_cfg.name,
        revision=model_cfg.revision,
        compute_dtype=model_cfg.compute_dtype,
        attn_implementation=model_cfg.attn_implementation,
        load_in_4bit=model_cfg.load_in_4bit,
        bnb_4bit_quant_type=model_cfg.bnb_4bit_quant_type,
        bnb_4bit_use_double_quant=model_cfg.bnb_4bit_use_double_quant,
        max_input_tokens=model_cfg.max_input_tokens,
        trust_remote_code=model_cfg.trust_remote_code,
        warmup_nan_guard=bool(model_cfg.get("warmup_nan_guard", True)),
    )
    name = str(model_cfg.name)
    if name.startswith("gemma"):
        return GemmaBackend(cfg)
    if name.startswith("llama"):
        return LlamaBackend(cfg)
    raise ValueError(f"Unknown model.name {name!r} — expected gemma* or llama*")


def _build_baseline(
    cfg: DictConfig,
    backend: Any,
    knowledge_store: KnowledgeStore,
    persona: Persona,
) -> Any:
    """Construct the configured baseline pipeline from `cfg.retrieval.type`."""
    rcfg = cfg.retrieval
    btype = str(rcfg.type)
    if btype == "b1":
        return VanillaRAG(
            backend=backend,
            knowledge_store=knowledge_store,
            top_k=int(rcfg.top_k),
            candidate_pool=int(rcfg.candidate_pool),
            alpha=rcfg.alpha,
            max_new_tokens=int(rcfg.max_new_tokens),
            max_input_tokens=int(rcfg.max_input_tokens),
        )
    if btype == "b2":
        few_shots_path = _resolve(rcfg.few_shots_dir) / f"{persona.persona_id}.yaml"
        few_shots = FewShotBundle.from_yaml(few_shots_path)
        return PromptPersonaRAG(
            backend=backend,
            knowledge_store=knowledge_store,
            few_shots=few_shots,
            top_k=int(rcfg.top_k),
            candidate_pool=int(rcfg.candidate_pool),
            alpha=rcfg.alpha,
            max_new_tokens=int(rcfg.max_new_tokens),
            max_input_tokens=int(rcfg.max_input_tokens),
            b2_variant=str(rcfg.b2_variant),
        )
    if btype == "m1":
        identity, self_facts, worldview, episodic = _build_typed_memory(cfg, persona)
        return TypedRetrievalRAG(
            backend=backend,
            knowledge_store=knowledge_store,
            identity_store=identity,
            self_facts_store=self_facts,
            worldview_store=worldview,
            episodic_store=episodic,
            use_identity_every_turn=bool(rcfg.use_identity_every_turn),
            use_epistemic_tags=bool(rcfg.use_epistemic_tags),
            use_episodic=bool(rcfg.use_episodic),
            write_episodic=bool(rcfg.write_episodic),
            epistemic_allowlist=tuple(rcfg.epistemic_allowlist),
            top_k_self_facts=int(rcfg.top_k_self_facts),
            top_k_worldview=int(rcfg.top_k_worldview),
            top_k_episodic=int(rcfg.top_k_episodic),
            top_k_knowledge=int(rcfg.top_k_knowledge),
            candidate_pool=int(rcfg.candidate_pool),
            alpha=rcfg.alpha,
            max_new_tokens=int(rcfg.max_new_tokens),
            max_input_tokens=int(rcfg.max_input_tokens),
            name=str(rcfg.name),
        )
    if btype == "m3":
        return _build_m3(cfg, backend, knowledge_store, persona)
    raise ValueError(f"Unknown retrieval.type {btype!r} — expected b1, b2, m1, or m3")


def _build_m3(
    cfg: DictConfig,
    backend: Any,
    knowledge_store: KnowledgeStore,
    persona: Persona,
) -> DriftGatedMechanism:
    """Construct the drift-gated mechanism from Hydra config.

    Builds the responder (typed-retrieval pipeline) on the same backend as
    the rest of the run. The gate judge and rerank judge load on demand;
    callers supply different model ids in the config, so we instantiate
    each through ``persona_rag.models``' factory pattern (mirroring
    ``_build_backend``).
    """
    from persona_rag.models import (
        GemmaBackend,
        HFBackendConfig,
        LlamaBackend,
        PrometheusBackend,
        QwenBackend,
    )

    rcfg = cfg.retrieval
    identity, self_facts, worldview, episodic = _build_typed_memory(cfg, persona)
    m1 = TypedRetrievalRAG(
        backend=backend,
        knowledge_store=knowledge_store,
        identity_store=identity,
        self_facts_store=self_facts,
        worldview_store=worldview,
        episodic_store=episodic,
        use_identity_every_turn=bool(rcfg.m1.use_identity_every_turn),
        use_epistemic_tags=bool(rcfg.m1.use_epistemic_tags),
        use_episodic=bool(rcfg.m1.use_episodic),
        write_episodic=bool(rcfg.m1.write_episodic),
        epistemic_allowlist=tuple(rcfg.m1.epistemic_allowlist),
        top_k_self_facts=int(rcfg.m1.top_k_self_facts),
        top_k_worldview=int(rcfg.m1.top_k_worldview),
        top_k_episodic=int(rcfg.m1.top_k_episodic),
        top_k_knowledge=int(rcfg.m1.top_k_knowledge),
        candidate_pool=int(rcfg.m1.candidate_pool),
        alpha=rcfg.m1.alpha,
        max_new_tokens=int(rcfg.m1.max_new_tokens),
        max_input_tokens=int(rcfg.m1.max_input_tokens),
    )

    def _build_judge_backend(model_id: str, name: str) -> Any:
        # Each backend's ``default_config`` carries per-model attention /
        # dtype overrides (Qwen2.5 needs SDPA, not eager — eager + fp16 +
        # 4-bit produces NaN logits on V100). Honour those.
        common_overrides: dict[str, object] = {
            "model_id": model_id,
            "name": name,
            "revision": None,
            "load_in_4bit": True,
            "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_use_double_quant": True,
            "max_input_tokens": 4096,
            "trust_remote_code": False,
            "warmup_nan_guard": True,
        }
        if name.startswith("qwen"):
            return QwenBackend(QwenBackend.default_config(**common_overrides))
        if name.startswith("prometheus"):
            return PrometheusBackend(PrometheusBackend.default_config(**common_overrides))
        if name.startswith("llama"):
            return LlamaBackend(LlamaBackend.default_config(**common_overrides))
        if name.startswith("gemma"):
            return GemmaBackend(GemmaBackend.default_config(**common_overrides))
        # Fallback: Llama loader covers most Mistral / decoder-only models
        # without special quirks. The HFBackendConfig path uses the shared
        # plumbing.
        judge_cfg = HFBackendConfig(
            **common_overrides,  # type: ignore[arg-type]
            compute_dtype="float16",
            attn_implementation="eager",
        )
        return LlamaBackend(judge_cfg)

    gate_judge = _build_judge_backend(str(rcfg.gate.judge_model), str(rcfg.gate.judge_name))
    rerank_judge = _build_judge_backend(
        str(rcfg.hybrid_ranker.rerank_judge.model_id),
        str(rcfg.hybrid_ranker.rerank_judge.name),
    )
    drift_gate = LlmJudgeDriftGate(
        judge=gate_judge,
        confidence_threshold=float(rcfg.gate.confidence_threshold),
        history_window=int(rcfg.gate.history_window),
        max_new_tokens=int(rcfg.gate.max_new_tokens),
        temperature=float(rcfg.gate.temperature),
    )
    char_rm = CharacterRMScorer(
        model_id=str(rcfg.hybrid_ranker.character_rm.model_id),
        device=str(rcfg.hybrid_ranker.character_rm.device),
        max_input_tokens=int(rcfg.hybrid_ranker.character_rm.max_input_tokens),
    )
    ranker = HybridRanker(
        character_rm=char_rm,
        rerank_judge=rerank_judge,
        weights=dict(rcfg.hybrid_ranker.weights),
        enabled_signals=tuple(rcfg.hybrid_ranker.enabled_signals),
        judge_max_new_tokens=int(rcfg.hybrid_ranker.rerank_judge.max_new_tokens),
        judge_temperature=float(rcfg.hybrid_ranker.rerank_judge.temperature),
    )
    extra_temps = tuple(float(t) for t in rcfg.extra_candidate_temperatures)
    return DriftGatedMechanism(
        backend=backend,
        m1=m1,
        drift_gate=drift_gate,
        hybrid_ranker=ranker,
        n_candidates=int(rcfg.n_candidates),
        extra_candidate_temperatures=extra_temps,
        extra_candidate_max_new_tokens=rcfg.extra_candidate_max_new_tokens,
        name=str(rcfg.name),
    )


def _build_typed_memory(
    cfg: DictConfig,
    persona: Persona,
) -> tuple[IdentityStore, SelfFactsStore, WorldviewStore, EpisodicStore]:
    """Open the four typed persona stores at `persona_store.persist_path` and index `persona`.

    Reuses one ChromaDB `PersistentClient` across all four stores by opening
    them at the same path. Indexing is idempotent by chunk id so re-runs are
    cheap.
    """
    ps_cfg = cfg.stores
    persist = _resolve(ps_cfg.persist_path)
    embedding_model = str(ps_cfg.embedding_model)
    identity = IdentityStore(persist, embedding_model=embedding_model)
    self_facts = SelfFactsStore(persist, embedding_model=embedding_model)
    worldview = WorldviewStore(persist, embedding_model=embedding_model)
    episodic = EpisodicStore(persist, embedding_model=embedding_model)

    chunks = chunk_persona(persona)
    counts = {
        "identity": identity.index(chunks),
        "self_facts": self_facts.index(chunks),
        "worldview": worldview.index(chunks),
        "episodic": episodic.index(chunks),
    }
    logger.info("typed memory indexed for {!r}: {}", persona.persona_id, counts)
    return identity, self_facts, worldview, episodic


def _init_wandb(cfg: DictConfig, persona_id: str) -> Any | None:
    """Initialise wandb if available + permitted by config; return run handle or None."""
    try:
        import wandb
    except ImportError:
        logger.warning("wandb not installed — skipping experiment logging")
        return None
    mode = os.environ.get("WANDB_MODE") or str(cfg.wandb.mode)
    run_name = (
        cfg.wandb.run_name_template
        if cfg.wandb.run_name_template is not None
        else f"{cfg.retrieval.name}_{cfg.model.name}_{persona_id}"
    )
    return wandb.init(
        project=cfg.wandb.project,
        entity=cfg.wandb.entity,
        mode=mode,
        name=str(run_name),
        config=OmegaConf.to_container(cfg, resolve=True),
        dir=str(_resolve(cfg.report_dir)),
    )


@dataclass(frozen=True)
class _ParsedQuery:
    """One test query parsed out of the Hydra config."""

    ix: int
    text: str
    bucket: str
    multi_seed: bool
    skip_in_matrix: bool


_VALID_BUCKETS = {
    "knowledge_grounded",
    "semantic_adjacent",
    "constraint_stressing",
    "appendix_contamination_demo",
}


def _parse_test_queries(raw: Any) -> list[_ParsedQuery]:
    """Validate + normalise the `test_queries` Hydra field.

    Each item is a mapping with a required `text` and `bucket`, and optional
    `multi_seed` (default false) and `skip_in_matrix` (default false). Anything
    else fails fast — there is no backward-compat for the old flat-list shape.
    """
    if raw is None:
        raise ValueError("test_queries is missing from the config")
    if isinstance(raw, str):
        raise ValueError("test_queries must be a list of mappings, not a string")
    out: list[_ParsedQuery] = []
    for ix, item in enumerate(raw):
        if not isinstance(item, dict | DictConfig):
            raise ValueError(
                f"test_queries[{ix}] must be a mapping with `text` and `bucket`, "
                f"got {type(item).__name__}"
            )
        text = item.get("text")
        bucket = item.get("bucket")
        if not text or not isinstance(text, str):
            raise ValueError(f"test_queries[{ix}].text must be a non-empty string")
        if bucket not in _VALID_BUCKETS:
            raise ValueError(
                f"test_queries[{ix}].bucket must be one of {sorted(_VALID_BUCKETS)}, got {bucket!r}"
            )
        out.append(
            _ParsedQuery(
                ix=ix,
                text=text,
                bucket=str(bucket),
                multi_seed=bool(item.get("multi_seed", False)),
                skip_in_matrix=bool(item.get("skip_in_matrix", False)),
            )
        )
    if not out:
        raise ValueError("test_queries is empty — at least one query is required")
    return out


def _seeds_for(query: _ParsedQuery, default_seed: int, multi_seeds: list[int]) -> list[int]:
    """Pick the seed list for one query: multi when `multi_seed`, else single."""
    if query.multi_seed:
        if not multi_seeds:
            raise ValueError(
                f"test_queries[{query.ix}] sets multi_seed=true but constraint_query_seeds is empty"
            )
        return list(multi_seeds)
    return [default_seed]


def _response_filename(query: _ParsedQuery, seed: int) -> str:
    """Single-shot queries → response_NN.json; multi-seed → response_NN_seedSSSS.json."""
    if query.multi_seed:
        return f"response_{query.ix:02d}_seed{seed:04d}.json"
    return f"response_{query.ix:02d}.json"


def _write_response(
    report_dir: Path,
    query: _ParsedQuery,
    seed: int,
    response: Response,
) -> Path:
    """Dump one query/response pair as JSON for later inspection. Returns the path."""
    out = report_dir / _response_filename(query, seed)
    payload = {
        "query": query.text,
        "bucket": query.bucket,
        "multi_seed": query.multi_seed,
        "skip_in_matrix": query.skip_in_matrix,
        "seed": seed,
        "text": response.text,
        "prompt_used": response.prompt_used,
        "metadata": response.metadata,
        "retrieved_knowledge": [
            {"id": c.id, "text": c.text, "metadata": c.metadata, "distance": c.distance}
            for c in response.retrieved_knowledge
        ],
        "retrieved_persona": {
            kind: [
                {"id": c.id, "text": c.text, "metadata": c.metadata, "distance": c.distance}
                for c in chunks
            ]
            for kind, chunks in response.retrieved_persona.items()
        },
        "steering_applied": response.steering_applied,
        "drift_signal": response.drift_signal,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


@hydra.main(version_base=None, config_path="../src/persona_rag/config", config_name="baseline")
def main(cfg: DictConfig) -> int:
    """Hydra entry point. Returns 0 on success, nonzero on failure."""
    report_dir = Path(cfg.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    logger.info("baseline run — report_dir={}", report_dir)
    logger.info("Resolved config:\n{}", OmegaConf.to_yaml(cfg, resolve=True))

    queries = _parse_test_queries(cfg.test_queries)
    default_seed = int(cfg.get("seed", 42))
    multi_seeds = [int(s) for s in cfg.get("constraint_query_seeds", [])]
    n_runs = sum(len(_seeds_for(q, default_seed, multi_seeds)) for q in queries)
    logger.info(
        "Parsed {} queries across buckets {} ({} total seed-runs).",
        len(queries),
        sorted({q.bucket for q in queries}),
        n_runs,
    )

    # Persona.
    persona_path = _resolve(cfg.personas_dir) / f"{cfg.persona_id}.yaml"
    persona = Persona.from_yaml(persona_path)
    logger.info("Persona loaded: {!r} ({})", persona.persona_id, persona.identity.name)

    # Knowledge store + corpus.
    ks_cfg = cfg.knowledge_store
    knowledge_store = KnowledgeStore(
        persist_path=_resolve(ks_cfg.persist_path),
        collection_name=str(ks_cfg.collection_name),
        embedding_model=str(ks_cfg.embedding_model),
        chunk_size=int(ks_cfg.chunk_size),
        chunk_overlap=int(ks_cfg.chunk_overlap),
    )
    corpus = _load_corpus(_resolve(ks_cfg.corpus_path))
    indexed = knowledge_store.index_corpus(corpus)
    logger.info(
        "Knowledge store: {} chunks indexed (total in collection: {})",
        indexed,
        knowledge_store.count(),
    )

    # Backend + baseline.
    backend = _build_backend(cfg.model)
    baseline = _build_baseline(cfg, backend, knowledge_store, persona)
    logger.info("Baseline: {} ({})", baseline.name, type(baseline).__name__)

    # wandb (best-effort).
    wandb_run = _init_wandb(cfg, persona.persona_id or "<unknown>")

    # Run queries — each (query, seed) combination is a separate run.
    summary: dict[str, Any] = {
        "persona_id": persona.persona_id,
        "backend": backend.name,
        "baseline": baseline.name,
        "queries_n": len(queries),
        "seed_runs_n": n_runs,
        "default_seed": default_seed,
        "constraint_query_seeds": multi_seeds,
        "results": [],
    }
    log_step = 0
    for query in queries:
        seeds = _seeds_for(query, default_seed, multi_seeds)
        for seed in seeds:
            logger.info(
                "Q{} [{}] seed={} (skip_in_matrix={}): {}",
                query.ix,
                query.bucket,
                seed,
                query.skip_in_matrix,
                query.text,
            )
            response = baseline.respond(query.text, persona, seed=seed)
            _write_response(report_dir, query, seed, response)
            summary["results"].append(
                {
                    "ix": query.ix,
                    "query": query.text,
                    "bucket": query.bucket,
                    "multi_seed": query.multi_seed,
                    "skip_in_matrix": query.skip_in_matrix,
                    "seed": seed,
                    "text_preview": response.text[:200],
                    "trimmed_chunks": response.metadata.get("trimmed_chunks"),
                    "retrieved_knowledge_n": len(response.retrieved_knowledge),
                }
            )
            if wandb_run is not None:
                wandb_run.log(
                    {
                        "step": log_step,
                        "query_ix": query.ix,
                        "seed": seed,
                        "trimmed_chunks": response.metadata.get("trimmed_chunks"),
                        "retrieved_knowledge_n": len(response.retrieved_knowledge),
                    }
                )
            log_step += 1

    summary_path = report_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    logger.info("Summary written to {}", summary_path)

    if wandb_run is not None:
        wandb_run.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
