"""Persona-vector validation script — separability gate.

Usage:

    # Default: prompt-scope, last-token pooling (replicates the published recipe).
    uv run python scripts/validate_persona_vectors.py model=gemma

    # Llama backend.
    uv run python scripts/validate_persona_vectors.py model=llama

    # Override sample size.
    uv run python scripts/validate_persona_vectors.py model=gemma n_pairs=100

    # Generation-scope re-extraction. Use a *different* cache dir so the
    # prompt-scope and generation-scope caches coexist.
    uv run python scripts/validate_persona_vectors.py model=gemma \\
        extraction_pool=mean extraction_scope=generation \\
        vectors_cache_dir=./.chroma/persona_vectors_generation

Runs the full persona-vector validation pipeline on every persona in
``personas/``:

    For each persona:
      1. Build a template-based contrast set (deterministic).
      2. Hash-based, reproducible prompt-disjoint train/test split.
      3. Extract persona vectors on the train split at each configured layer.
      4. Extract persona vectors on the test split (reusing centroids).
      5. Run the linear-separability probe with shuffled-label and
         random-feature controls; report per-layer AUROC.
      6. Cache train-extraction vectors + metadata under
         ``vectors_cache_dir``.
      7. UMAP-project in/out test activations at the best layer; save PNG.

    Across personas:
      8. Pick a single global best layer (mean-AUROC argmax).
      9. Patch that layer into every persona's meta.json sidecar.
     10. Write structured ``per_layer_auroc.json`` and human-readable
         ``verdict.md`` summaries.

Exit code: 0 on confirmed/weak verdict, 2 on refuted.

This script imports from ``persona_rag.models``; on macOS the import fails
because ``bitsandbytes`` is unavailable. Run on a CUDA host.

NOTE on generation-scope extraction: ``extraction_scope=generation`` is
~50x slower than ``prompt`` (it runs a 64-token greedy generate per contrast
prompt instead of a single forward pass). Budget accordingly.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import hydra
import numpy as np
from loguru import logger
from omegaconf import DictConfig, OmegaConf

from persona_rag.schema.persona import Persona
from persona_rag.vectors.cache import save_persona_vectors, update_best_layer
from persona_rag.vectors.contrast_prompts import ContrastPromptGenerator
from persona_rag.vectors.drift import DriftSignal
from persona_rag.vectors.extractor import PersonaVectorExtractor
from persona_rag.vectors.layer_selection import pick_global_best_layer
from persona_rag.vectors.probe import (
    AUROC_CONFIRMED_FLOOR,
    AUROC_WEAK_FLOOR,
    SeparabilityProbe,
)

PERSONAS_DIR = Path(__file__).resolve().parents[1] / "personas"


def _load_backend(cfg: DictConfig) -> Any:
    """Load the LLM backend from Hydra config.

    Imports are scoped inside the function so importing this script on a
    Darwin dev machine (no bitsandbytes) does not raise at import time —
    only when the script is actually executed does the backend load.

    Mirrors ``scripts/smoke_test_models.py::_build_backend`` so every entry
    point uses the same shared ``HFBackendConfig`` shape (per-model ``name``
    is e.g. ``gemma2-9b-it`` / ``llama3.1-8b-instruct``).
    """
    from persona_rag.models import HFBackendConfig
    from persona_rag.models.gemma import GemmaBackend
    from persona_rag.models.llama import LlamaBackend

    model_cfg = cfg.model
    backend_cfg = HFBackendConfig(
        model_id=model_cfg.model_id,
        name=model_cfg.name,
        revision=model_cfg.get("revision"),
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
        return GemmaBackend(backend_cfg)
    if name.startswith("llama"):
        return LlamaBackend(backend_cfg)
    raise ValueError(f"unsupported model name: {name!r}")


def _umap_figure(
    in_states: np.ndarray,
    out_states: np.ndarray,
    persona_id: str,
    layer: int,
    out_path: Path,
) -> None:
    """Save a 2-D UMAP projection of in/out test activations.

    UMAP is seeded for reproducibility. This figure is diagnostic: a reviewer
    inspects it to confirm in/out clusters *look* separable (the AUROC number
    alone can be misleading on small test sets).
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from umap import UMAP

    stacked = np.vstack([in_states, out_states])
    labels = np.concatenate([np.ones(in_states.shape[0]), np.zeros(out_states.shape[0])])
    n_neighbors = min(15, max(2, stacked.shape[0] - 1))
    reducer = UMAP(random_state=42, n_neighbors=n_neighbors, metric="cosine")
    proj = reducer.fit_transform(stacked)

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(proj[labels == 1, 0], proj[labels == 1, 1], label="in-persona", alpha=0.7, s=18)
    ax.scatter(proj[labels == 0, 0], proj[labels == 0, 1], label="out-persona", alpha=0.7, s=18)
    ax.set_title(f"{persona_id} — UMAP(hidden states, layer {layer})")
    ax.set_xlabel("UMAP-1")
    ax.set_ylabel("UMAP-2")
    ax.legend()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


@hydra.main(
    config_path="../src/persona_rag/config", config_name="validate_vectors", version_base=None
)
def main(cfg: DictConfig) -> int:
    """Run validation end-to-end and return a process exit code."""
    report_dir = Path(cfg.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    logger.add(report_dir / "validation.log", level="DEBUG")

    logger.info("validation start. Config:\n{}", OmegaConf.to_yaml(cfg))
    (report_dir / "config.yaml").write_text(OmegaConf.to_yaml(cfg), encoding="utf-8")

    backend = _load_backend(cfg)
    gen = ContrastPromptGenerator(backend, n_pairs=int(cfg.n_pairs), seed=int(cfg.seed))
    extractor = PersonaVectorExtractor(
        backend,
        layers=list(cfg.layers),
        pool=cfg.extraction_pool,
        scope=cfg.extraction_scope,
        n_pairs=int(cfg.n_pairs),
        seed=int(cfg.seed),
    )
    probe = SeparabilityProbe(seed=int(cfg.seed))
    cache_dir = Path(cfg.vectors_cache_dir)

    persona_paths = sorted(PERSONAS_DIR.glob("*.yaml"))
    if not persona_paths:
        logger.error("no personas at {}", PERSONAS_DIR)
        return 2
    logger.info(
        "running validation on {} personas: {}",
        len(persona_paths),
        [p.stem for p in persona_paths],
    )

    per_persona_auroc: dict[str, dict[int, float]] = {}
    per_persona_results: dict[str, Any] = {}
    worst_verdict = "confirmed"  # we track the worst verdict across personas

    for path in persona_paths:
        persona = Persona.from_yaml(path)
        pid = persona.persona_id or path.stem
        persona_dict = persona.model_dump(mode="json")

        contrast_set = gen.generate(persona_dict)
        train_set, test_set = contrast_set.split(float(cfg.test_fraction), seed=int(cfg.seed))
        logger.info(
            "persona {!r}: contrast split train={} test={} (sha256={})",
            pid,
            train_set.n_pairs,
            test_set.n_pairs,
            contrast_set.sha256()[:12],
        )

        train_vectors = extractor.extract(persona_dict, train_set)
        test_vectors = extractor.extract(persona_dict, test_set)
        result = probe.train_and_evaluate(train_vectors, test_vectors)

        # Cache train-split extraction (consumed by downstream drift gating).
        save_persona_vectors(
            train_vectors,
            cache_dir,
            best_layer=result.best_layer,
            store_full_activations=bool(cfg.store_full_activations),
        )

        # UMAP figure at this persona's best layer — diagnostic for the
        # author's review checklist.
        bl = result.best_layer
        _umap_figure(
            test_vectors.in_states[bl].numpy(),
            test_vectors.out_states[bl].numpy(),
            persona_id=pid,
            layer=bl,
            out_path=report_dir / f"umap_{pid}_layer{bl}.png",
        )

        # Sanity-check the drift signal on this persona at best_layer.
        drift = DriftSignal.from_persona_vectors(train_vectors, bl)
        d_in = drift.compute(train_vectors.in_persona_centroid[bl])
        d_out = drift.compute(train_vectors.out_persona_centroid[bl])
        logger.info(
            "persona {!r}: drift-signal sanity — in_centroid={:+.3f} (want +1.000), "
            "out_centroid={:+.3f} (want -1.000)",
            pid,
            d_in,
            d_out,
        )

        per_persona_auroc[pid] = dict(result.per_layer_auroc)
        per_persona_results[pid] = {
            "per_layer_auroc": result.per_layer_auroc,
            "per_layer_train_auroc": result.per_layer_train_auroc,
            "best_layer": result.best_layer,
            "best_auroc": result.best_auroc,
            "shuffled_label_auroc": result.shuffled_label_auroc,
            "random_feature_auroc": result.random_feature_auroc,
            "verdict": result.verdict,
            "notes": result.notes,
            "drift_sanity": {"in_centroid": d_in, "out_centroid": d_out},
            "contrast_set_sha256": contrast_set.sha256(),
        }
        # Worst-verdict tracking.
        if result.verdict == "refuted":
            worst_verdict = "refuted"
        elif result.verdict == "weak" and worst_verdict == "confirmed":
            worst_verdict = "weak"

    # Global best layer across personas.
    global_pick = pick_global_best_layer(per_persona_auroc)
    for pid in per_persona_results:
        update_best_layer(cache_dir, pid, global_pick.best_layer)

    # Structured summary — replayable to wandb later (author's Q3 refinement).
    summary: dict[str, Any] = {
        "script": "scripts/validate_persona_vectors.py",
        "backend": {
            "name": getattr(backend, "name", "?"),
            "model_id": getattr(backend, "model_id", "?"),
            "num_layers": getattr(backend, "num_layers", None),
            "hidden_dim": getattr(backend, "hidden_dim", None),
        },
        "config": {
            "n_pairs": int(cfg.n_pairs),
            "pool": str(cfg.extraction_pool),
            "scope": str(cfg.extraction_scope),
            "layers": list(cfg.layers),
            "seed": int(cfg.seed),
            "test_fraction": float(cfg.test_fraction),
        },
        "thresholds": {
            "confirmed_floor": AUROC_CONFIRMED_FLOOR,
            "weak_floor": AUROC_WEAK_FLOOR,
        },
        "per_persona": per_persona_results,
        "global_best_layer": asdict(global_pick),
        "overall_verdict": worst_verdict,
    }
    (report_dir / "per_layer_auroc.json").write_text(
        json.dumps(summary, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )

    # Human-readable verdict note.
    lines = [
        f"# Persona-Vector Validation — {worst_verdict.upper()}",
        "",
        f"- Backend: `{summary['backend']['name']}` ({summary['backend']['model_id']})",
        f"- Layers swept: {list(cfg.layers)}",
        f"- Pool / scope: `{cfg.extraction_pool}` / `{cfg.extraction_scope}`",
        f"- Contrast pairs: {int(cfg.n_pairs)} per persona, test_fraction={float(cfg.test_fraction):.2f}",
        f"- Seed: {int(cfg.seed)}",
        "",
        f"**Global best layer: {global_pick.best_layer} (mean AUROC {global_pick.mean_auroc:.3f})**",
        "",
    ]
    if global_pick.gap_warning:
        lines.append(f"> NOTE: {global_pick.gap_warning}")
        lines.append("")
    lines.append("## Per-persona")
    lines.append("")
    lines.append(
        "| Persona | Best layer | Best AUROC | Verdict | Shuffled-label (max) | Random-feature |"
    )
    lines.append("|---|---|---|---|---|---|")
    for pid, res in per_persona_results.items():
        lines.append(
            "| `{pid}` | {best_layer} | {best_auroc:.3f} | {verdict} | {shuffled_max:.3f} | {rand:.3f} |".format(
                pid=pid,
                best_layer=res["best_layer"],
                best_auroc=res["best_auroc"],
                verdict=res["verdict"],
                shuffled_max=max(res["shuffled_label_auroc"].values()),
                rand=res["random_feature_auroc"],
            )
        )
    lines.append("")
    lines.append("## Verdict rule")
    lines.append("")
    lines.append(f"- AUROC ≥ {AUROC_CONFIRMED_FLOOR:.2f} → confirmed")
    lines.append(f"- {AUROC_WEAK_FLOOR:.2f} ≤ AUROC < {AUROC_CONFIRMED_FLOOR:.2f} → weak")
    lines.append(f"- AUROC < {AUROC_WEAK_FLOOR:.2f} → refuted (fall back to v0.2)")
    (report_dir / "verdict.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info("validation complete: verdict = {}", worst_verdict)
    return 0 if worst_verdict != "refuted" else 2


if __name__ == "__main__":
    sys.exit(main())
