# Persona-RAG

Persona-conditioned retrieval for identity-consistent generation. A research
project exploring whether grounding RAG retrieval in a structured persona
representation produces more identity-consistent responses than prompt-only or
fine-tuning approaches.

## Running models

This project ships two LLM backends, both loaded in 4-bit NF4 (with
double-quant) and a compute dtype read from a Hydra config:

- `gemma` → `google/gemma-2-9b-it`
- `llama` → `meta-llama/Llama-3.1-8B-Instruct`

### Prerequisites

- Python 3.11 (the repo sets `.python-version`).
- [`uv`](https://docs.astral.sh/uv/) for dependency management.
- A CUDA GPU for real inference. Local dev machines can import the package and
  run the non-GPU test suite; actual model loading requires CUDA
  (`bitsandbytes` is CUDA-only).
- Access to both gated HF models. Log in once per machine:
  ```bash
  huggingface-cli login
  # then accept model licenses on huggingface.co for google/gemma-2-9b-it and
  # meta-llama/Llama-3.1-8B-Instruct
  ```

### Setup

```bash
# in the repo root
uv venv --python 3.11
uv pip install -e '.[dev]'
```

On a CUDA host, `bitsandbytes` is installed automatically from the dependency
list. On macOS, it is skipped (`bitsandbytes` has no CPU/MPS build) — the
non-GPU tests still run, and anything that doesn't load weights still works.

### Smoke-testing a backend

The smoke test runs a multi-prompt stability suite against the selected
backend, checks logits for NaN/inf, verifies greedy-decoding reproducibility
across repeated runs, exercises hidden-state capture, and enforces a peak-memory
budget. The exit code is non-zero on any gate failure.

```bash
# on a CUDA host
uv run python scripts/smoke_test_models.py model=gemma
uv run python scripts/smoke_test_models.py model=llama
```

Artifacts land in `results/smoke_<model>/`:

- `load_report.json` — model id, dtype, attention impl, transformers / torch
  versions, peak memory.
- `stability_results.json` — per-prompt output, coherence flag, logit stats,
  timing.
- `stability_outputs.txt` — human-readable dump for spot-checking.
- `reproducibility.json` — greedy token-match rate across repeated runs.
- `hidden_states_check.json` — per-layer shapes plus NaN/inf checks.
- `summary.json` — pass/fail per gate, at-a-glance.
- `smoke_test.log` — full `loguru` log.

### Using a backend from Python

```python
from persona_rag.models import ChatMessage, GenerationConfig, load_backend

gemma = load_backend("gemma")   # or "llama"

# single-prompt generation
text = gemma.generate(
    "The capital of Norway is",
    cfg=GenerationConfig(max_new_tokens=32, do_sample=False, seed=0),
)

# chat-style generation (system role is folded into the first user turn on
# Gemma 2, which has no native system role).
reply = gemma.chat(
    [
        ChatMessage("system", "You are a concise assistant."),
        ChatMessage("user", "Explain in one sentence what RAG is."),
    ],
    cfg=GenerationConfig(max_new_tokens=64, seed=0),
)

# Hidden-state capture for downstream geometry / probe work.
hs = gemma.get_hidden_states("Hello, world.", layers=[15])
# hs[15].shape == (seq_len, hidden_dim)
```

## Personas

A persona is a typed YAML document under [`personas/`](personas/) that the
four typed memory stores chunk, embed, and serve to retrieval mechanisms. Three
public example personas ship:
[`cs_tutor.yaml`](personas/cs_tutor.yaml),
[`historian.yaml`](personas/historian.yaml),
[`climate_scientist.yaml`](personas/climate_scientist.yaml).

### Authoring a new persona

1. Copy one of the examples to `personas/<your_name>.yaml`. The filename stem
   becomes the `persona_id` (override by adding an explicit `persona_id:` key).
2. Fill in the four required sections:
   - `identity:` — `name`, `role`, `background`, and a list of `constraints`
     (negative rules; ≤ 200 chars each).
   - `self_facts:` — list of `{fact, confidence?}` items (≤ 500 chars).
     `epistemic` is fixed to `"fact"` on self-facts and defaults to 1.0
     confidence.
   - `worldview:` — list of
     `{claim, domain, epistemic?, valid_time?, confidence?}` items. `domain`
     is a snake_case slug. `epistemic` is one of
     `fact | belief | hypothesis | contested` (default `belief`). `valid_time`
     follows the grammar `always | YYYY | YYYY-YYYY | YYYY-`.
   - `episodic:` — optional; starts empty and is written at runtime by the
     episodic store.
3. Unknown top-level keys are **rejected** — typos surface as validation
   errors.
4. Private personas go in `personas/private/` (gitignored).

### Registering and querying personas

```python
from persona_rag.schema import PersonaRegistry
from persona_rag.stores import (
    EpisodicStore, IdentityStore, SelfFactsStore, WorldviewStore,
)

path = "./.chroma/persona"
registry = PersonaRegistry(
    identity_store=IdentityStore(path),
    self_facts_store=SelfFactsStore(path),
    worldview_store=WorldviewStore(path),
    episodic_store=EpisodicStore(path),
    # vector_extractor=PersonaVectorExtractor(...),  # optional
)

reg = registry.register("personas/cs_tutor.yaml")

# Identity is always retrieved (full identity + constraint set per persona).
identity_chunks = reg.identity_store.get_all(persona_id="cs_tutor")

# Worldview top-k with epistemic filter and `as_of` year for bi-temporal claims.
wv = reg.worldview_store.query(
    "European history", top_k=5, persona_id="historian",
    epistemic=["fact", "belief"], as_of="1750",
)

# Episodic entries are runtime-writable and decay-ranked.
from datetime import datetime, timezone
from persona_rag.schema.chunker import PersonaChunk
now = datetime.now(timezone.utc)
reg.episodic_store.write(PersonaChunk(
    id="cs_tutor:episodic:0",
    text="User asked about Rust concurrency patterns.",
    kind="episodic",
    metadata={
        "persona_id": "cs_tutor", "kind": "episodic",
        "timestamp": now.isoformat(), "decay_t0": now.isoformat(),
        "turn_id": "7",
    },
))
```

The four ChromaDB collections live under `persist_path` (default
`./.chroma/persona`, gitignored). Re-registration is idempotent — chunks are
keyed by `{persona_id}:{kind}:{n}` and upserted in place.

## Persona vectors

Persona vectors (Anthropic, [arXiv 2507.21509](https://arxiv.org/abs/2507.21509))
are the drift-signal infrastructure the retrieval mechanisms condition on.
Extraction runs the backend on a contrastive prompt set (in-persona vs
out-of-persona), pools at the final prompt token, and computes the per-layer
mass-mean direction `mean(in_states) − mean(out_states)`. Cached vectors and
centroids land under `./.chroma/persona_vectors/<persona_id>.safetensors` plus
a sibling `.meta.json` (the `PersonaRegistry` hook surfaces them to downstream
code).

A linear-separability probe with shuffled-label and random-feature controls is
computed on a prompt-disjoint held-out split. Verdict thresholds (configurable
in the validation Hydra config):

- `AUROC ≥ 0.80` → `confirmed`.
- `0.70 ≤ AUROC < 0.80` → `weak`; document in limitations.
- `AUROC < 0.70` → `refuted`; fall back to an LLM-as-judge re-rank.

Run on a CUDA host:

```bash
uv run python scripts/validate_persona_vectors.py model=gemma
# Optional ablation — run the paper-default pool/scope vs generation-scope mean:
uv run python scripts/validate_persona_vectors.py model=gemma \
    extraction_pool=mean extraction_scope=generation layers="[<best_layer>]"
```

Artifacts under `results/a11_validation/<timestamp>/`:

- `per_layer_auroc.json` — structured summary (per-persona AUROCs, best layer,
  controls, verdict).
- `verdict.md` — human-readable summary.
- `umap_<persona_id>_layer<N>.png` — 2-D projection of test-split activations.
- `config.yaml` + `validation.log` — full run config and `loguru` log.

Exit code is the verdict: `0` on `confirmed` / `weak`, `2` on `refuted`.

### Using persona vectors from Python

```python
from persona_rag.models import load_backend
from persona_rag.vectors import (
    DriftSignal, PersonaVectorExtractor, load_persona_vectors,
)

# One-time: extract and cache (expensive — ~seconds per persona on a V100).
backend = load_backend("gemma")
extractor = PersonaVectorExtractor(backend, layers=[8, 12, 16, 20])
# ... or load a previously-cached set ...
pv = load_persona_vectors("./.chroma/persona_vectors", persona_id="cs_tutor")

# Inference-time drift signal.
drift = DriftSignal.from_persona_vectors(pv, layer=pv.metadata["best_layer"])
hidden = backend.get_hidden_states("User turn here.", layers=[drift.layer])[drift.layer]
print(drift.compute(hidden))  # ∈ [-1, 1]; +1 = fully on-persona, -1 = off
```

## Running baselines

Two end-to-end baseline retrieval pipelines ship on top of a hybrid (BM25 +
dense) knowledge store:

- **Vanilla RAG**: hybrid retrieval, neutral system prompt, persona ignored.
- **Prompt-persona**: hybrid retrieval plus a structured persona system block
  (identity / self-facts / worldview / constraints) and hand-authored dialogue
  few-shot exchanges per persona, including at least one constraint-case
  example (off-domain deflection or contested-topic flag).

### Knowledge store

The knowledge store is a ChromaDB collection (`knowledge_chunks`) under a
`PersistentClient` at `.chroma/knowledge/`, kept separate from the persona
stores. Embedder: `BAAI/bge-small-en-v1.5`. Chunker: LlamaIndex
`SentenceSplitter` (chunk_size=512, chunk_overlap=50). BM25 via `bm25s` is
built in-memory parallel to dense; hybrid retrieval fuses the two via
Reciprocal Rank Fusion (RRF, k=60 default per Cormack et al. 2009). A
weighted-sum ablation toggle is available via `alpha` ∈ [0, 1].

The dev corpus lives at `benchmarks_data/knowledge_corpora/cs_tutor/` (short
hand-authored markdown snippets on distributed-systems topics, paraphrased to
avoid copyright issues).

### Few-shot bundles for the prompt-persona pipeline

Each persona ships dialogue few-shots at `personas/examples/<persona_id>.yaml`,
schema-validated by `FewShotBundle`. Authoring rules:
- Each exchange is a `user → assistant` pair, drawn from the persona's actual
  `self_facts` / `worldview` / `constraints` rather than generic content.
- At least one exchange per persona has `is_constraint_case: true` — the
  persona's no-go demonstration.
- The renderer orders constraint-case few-shots last so they're freshest in
  the model's context.

### Running

```bash
# Vanilla RAG (default), Gemma 2, cs_tutor persona, dev corpus
uv run python scripts/run_baseline.py

# Prompt-persona, Llama 3.1, historian persona
uv run python scripts/run_baseline.py model=llama retrieval=prompt_persona persona_id=historian

# Audit-only one-liner persona-prefix variant (for strength comparisons)
uv run python scripts/run_baseline.py retrieval=prompt_persona retrieval.b2_variant=v02_one_liner

# Weighted-sum hybrid fusion ablation (alpha=0.5)
uv run python scripts/run_baseline.py retrieval.alpha=0.5

# wandb online (server)
WANDB_MODE=online uv run python scripts/run_baseline.py wandb.mode=online
```

Per-query response artefacts (`response_NN.json` for single-shot queries,
`response_NN_seedSSSS.json` for multi-seed constraint-stressing queries) and a
`summary.json` land under
`results/baseline_pilot/<timestamp>_<retrieval>_<model>_<persona>/`.

### Test-query schema

`baseline.yaml` carries `test_queries` as a list of mappings, each with a
required `text` and `bucket`, and optional `multi_seed` and `skip_in_matrix`
flags. Buckets:

- `knowledge_grounded` — exercises hybrid retrieval; expects citation discipline.
- `semantic_adjacent` — adjacent to persona worldview but lexically distinct
  from any few-shot user turn; tests voice generalisation.
- `constraint_stressing` — pressures the persona's no-go zones; typically
  flagged `multi_seed: true` so the pipeline runs once per seed in
  `constraint_query_seeds` and the deflection rate is measurable across runs.
- `appendix_contamination_demo` — kept on disk to demonstrate few-shot replay
  behaviour; flagged `skip_in_matrix: true` so headline comparison-matrix
  builds exclude it without losing the audit trail.

### Strength-comparison audit

The prompt-persona pipeline has an audit-only variant
(`retrieval.b2_variant=v02_one_liner`) that swaps the structured persona
block for a naïve one-liner persona prefix. Use it to validate that the
structured variant is measurably stronger than a one-liner — never use it in
headline results. Compare contradiction-rate and persona-adherence between
the two variants over a fixed pilot set of conversations × personas.

## Tests

```bash
# local / fast tier (no GPU needed) — always runnable
uv run pytest

# slow + GPU tier (runs only on a CUDA host with HF_TOKEN set)
uv run pytest -m "slow and gpu"
```

## Hardware notes

- V100 (compute capability 7.0) has no hardware bfloat16. The shipped Hydra
  configs default to `compute_dtype: float16`. Flip to `bfloat16` when moving
  to Ampere+ / H100 — no code change needed.
- `attn_implementation: eager` is required on Gemma 2 for softcapping
  correctness, and is mirrored on Llama 3.1 so the attention-kernel path is
  identical across backbones.
- `transformers>=4.49` is required for the Gemma 2 fp16 sliding-window-mask
  fix.

## Repository layout

```
persona-rag/
├── src/persona_rag/
│   ├── schema/          # persona / conversation Pydantic schemas + chunker + registry
│   ├── stores/          # typed ChromaDB-backed memory stores
│   ├── models/          # base interface + 4-bit HF backends (Gemma, Llama)
│   ├── vectors/         # persona-vector extraction, probe, drift signal, cache
│   ├── evaluation/      # smoke-test stability suite
│   └── config/          # Hydra configs
├── personas/            # public example personas (private personas under personas/private/)
├── benchmarks_data/     # hand-authored evaluation data
├── tests/
├── results/             # gitignored except whitelisted configs
├── scripts/             # CLI entry points (smoke / validation / verification)
├── pyproject.toml
└── README.md
```

## License

To be added.
