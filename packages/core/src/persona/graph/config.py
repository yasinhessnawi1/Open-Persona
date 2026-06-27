"""Env-driven configuration for the knowledge graph (Spec K0).

All graph thresholds are config, never hardcoded (D-K0-1/2/9): the make-or-break
coherence parameters can only be truly tuned against real accumulation, so a
re-tune is a config change, not a code change. Defaults are the bge-small-band
starting priors from research §2.3 (cosine compresses to ~[0.6, 1.0] for this
embedder); the sweep harness (:mod:`persona.graph.calibration`) re-derives the
operating point from labelled data. F0.5 precision bias is encoded by the HIGH
auto-merge bar + the WIDE review band down to the separate bar (a wrong merge is
catastrophic and transitive; a too-shy split is recoverable — research §2.4).

T5 ships the entity-resolution fields; the merge / semantic-link (T6) and
index-backend (T7) fields are added by those tasks (additive).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["GraphSettings"]


class GraphSettings(BaseSettings):
    """Knowledge-graph tunables, read from ``PERSONA_GRAPH_*`` env vars.

    Attributes:
        alias_merge_threshold: Auto-merge upper bar — a mention this confident an
            alias of a known entity resolves ``MERGE`` (the Fellegi-Sunter
            auto-link zone). High by design (precision bias). For the deterministic
            resolver this gates *embedding* agreement; an exact normalized match
            always merges regardless.
        alias_separate_threshold: Lower bar — below this, confidently not a known
            entity (``SEPARATE``). Between the two bars is the review band
            (``AMBIGUOUS``) handed to K2's LLM judge.
        alias_candidate_limit: How many nearest registry entities the embedding
            candidate-gen returns to score (the blocking step; Graphiti uses 15).
    """

    model_config = SettingsConfigDict(env_prefix="PERSONA_GRAPH_", extra="ignore")

    alias_merge_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    alias_separate_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    alias_candidate_limit: int = Field(default=15, gt=0)

    # --- merge engine (T6) -------------------------------------------------
    # merge_extend_threshold (D-K0-1) is THE make-or-break coherence parameter:
    # new knowledge whose embedding is this close to an existing node EXTENDS it,
    # else a new node is created. F0.5 precision-biased and **flagged for a
    # real-data re-tune** (only true accumulation can tune it). semantic_link_*
    # (D-K0-2) govern the automatic baseline links: looser than the merge bar (a
    # navigable link, not a merge), capped per node so the graph stays navigable.
    merge_extend_threshold: float = Field(default=0.88, ge=0.0, le=1.0)
    semantic_link_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    max_semantic_links: int = Field(default=8, gt=0)

    # --- dense index (T7, D-K0-6/7) ----------------------------------------
    # pgvector is the DEFAULT and the only wired prod path for v0.1 (exact, mature,
    # already in the stack). turbovec is the opt-in quantized in-RAM index — never a
    # hard dependency (lazy-imported, behind the [turbovec] extra). On the turbovec
    # path the exact-rerank is MANDATORY (the 0.95 recall gate only holds with it),
    # so 4-bit is a memory/speed choice, not a precision one (D-K0-7).
    index_backend: Literal["pgvector", "turbovec"] = "pgvector"
    index_bit_width: int = Field(default=4, ge=2, le=4)
    rerank_n: int = Field(default=50, gt=0)
    index_path: str | None = None
    # Cold-start floor (D-K0-6 / research §5): below this node count a user runs on
    # pgvector (turbovec's TQ+ calibration freezes badly under it); cross it and the
    # turbovec index is rebuilt once from Postgres. The trigger is operational; the
    # mechanism is GraphStore.rebuild_index.
    turbovec_calibration_min: int = Field(default=1000, gt=0)

    # --- hybrid retrieval (K1, D-K1-2/-4/-5) -------------------------------
    # RRF fuses the dense (already exact-reranked) + sparse (FTS) legs in
    # parallel, never gated (spec §4). rank-based — no score normalization. k=60
    # is the Cormack 2009 / Persona-RAG default; per-leg weights default to parity
    # and exist so a real-data re-tune is a config change (the D-K0-1 posture).
    # The per-leg over-fetch pools are deliberately larger than result_budget so
    # fusion + the post-fusion K4 subtraction filter (D-K1-7) + truncation still
    # fill the budget without starving on subtractions.
    rrf_k: int = Field(default=60, gt=0)
    dense_weight: float = Field(default=1.0, ge=0.0)
    sparse_weight: float = Field(default=1.0, ge=0.0)
    result_budget: int = Field(default=10, gt=0)
    dense_pool: int = Field(default=50, gt=0)
    sparse_pool: int = Field(default=50, gt=0)

    # --- link-aware traversal (K1, D-K1-3) ---------------------------------
    # Bounded one-hop expansion over the top fused nodes, TYPE-weighted, with a
    # two-level cap (per-node `neighbors` limit at the source + a per-query budget)
    # so a densely-linked entity cannot flood. Neighbours enter at a discounted
    # rank AFTER the fused core (augment-never-displace; the final truncation
    # enforces it). seed_count=0 OR budget=0 disables traversal with no code change.
    # Type weights default ENTITY > CAUSAL ≈ TEMPORAL > SEMANTIC (semantic
    # neighbours are already what the dense leg surfaces → lowest, avoid
    # double-counting meaning).
    traversal_seed_count: int = Field(default=3, ge=0)
    traversal_per_node: int = Field(default=5, gt=0)
    traversal_budget: int = Field(default=10, ge=0)
    traversal_weight_entity: float = Field(default=1.0, ge=0.0)
    traversal_weight_causal: float = Field(default=0.8, ge=0.0)
    traversal_weight_temporal: float = Field(default=0.8, ge=0.0)
    traversal_weight_semantic: float = Field(default=0.4, ge=0.0)

    # --- graph-aware injection gate (K3, D-K3-3) ---------------------------
    # K3 injects a retrieved node into the prompt only when it is RELEVANT to the
    # current turn — gating on the genuine relevance signal, dense cosine
    # similarity (``1 - node.distance``), NEVER on the RRF ``score`` (rank-based:
    # rank-1 small talk scores identically to rank-1 relevant, so an RRF floor
    # stuffs every turn — fusion.py / the RRF literature).
    #
    # ``inject_similarity_floor`` is **validated by the calibration sweep**, NOT
    # inherited from K0's 0.82 semantic-link bar ("linkable concepts" is a
    # SYMMETRIC concept↔concept decision; "inject-worthy" is the ASYMMETRIC
    # query↔node-content retrieval decision, which scores materially lower on
    # bge-small — production embeds the raw query (store.py) against the raw node
    # content (merge.py), no instruction prefix). The sweep over a labelled
    # relevant-vs-small-talk set (F0.5, precision-biased — stuffing degrades every
    # turn, the recoverable failure is starving) put the operating point at the
    # 0.62–0.66 plateau: at 0.66, precision 0.86 / recall 0.50, cleanly excluding
    # ALL small talk (≤0.46) and most loosely-related topical overlap (the
    # stuffing guard). Evidence: docs/specs/phase3/spec_K3/evidence/. Flagged for a
    # real-data re-tune (the K0 posture) — the moderate recall is the recoverable
    # side, and the sparse-only fallback below catches exact-term hits dense
    # paraphrase-misses.
    #
    # Sparse-only nodes (no embedding distance) get a NARROW high-precision
    # fallback: inject only a top exact-term FTS hit (names/drugs that paraphrase-
    # blind dense retrieval misses), capped tight so it cannot become a stuffing
    # vector — its regression sentinel is the eval's ``small_talk_injection == 0``.
    inject_similarity_floor: float = Field(default=0.66, ge=0.0, le=1.0)
    inject_sparse_rank_cap: int = Field(default=3, gt=0)

    @model_validator(mode="after")
    def _bands_ordered(self) -> GraphSettings:
        if self.alias_separate_threshold > self.alias_merge_threshold:
            msg = (
                "alias_separate_threshold must be <= alias_merge_threshold "
                f"(got {self.alias_separate_threshold} > {self.alias_merge_threshold})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _at_least_one_leg_weighted(self) -> GraphSettings:
        # Both weights ge 0 individually, but both-zero is a no-signal config that
        # collapses RRF scoring to a constant — reject it (every node would tie).
        if self.dense_weight == 0.0 and self.sparse_weight == 0.0:
            msg = "dense_weight and sparse_weight cannot both be 0 (it collapses RRF scoring)"
            raise ValueError(msg)
        return self
