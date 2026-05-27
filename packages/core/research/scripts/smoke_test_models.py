"""Backend smoke test — stability gate for a 4-bit HF backend on a CUDA host.

Runs a multi-prompt stability suite from ``persona_rag.evaluation.smoke_suite``
against the selected backend, checks logits for NaN/inf, runs a
greedy-reproducibility check, exercises hidden-state capture, and writes
artifacts to ``${report_dir}``:

- ``load_report.json``         — model / dtype / attn / memory / version metadata.
- ``stability_results.json``   — per-prompt results: output, coherence flag, timing.
- ``stability_outputs.txt``    — human-readable dump for spot-checking.
- ``reproducibility.json``     — greedy token-match rate across repeated runs.
- ``hidden_states_check.json`` — shapes and dtype of captured layer activations.
- ``smoke_test.log``           — full loguru log.

Exit code:
- 0 if every gate passes.
- 1 if any gate fails (NaN/inf in logits, coherence under bar, reproducibility
  under hard-min, peak memory over budget, or a hard crash during the suite).

Usage:
    uv run python scripts/smoke_test_models.py                  # defaults to model=gemma
    uv run python scripts/smoke_test_models.py model=llama
    uv run python scripts/smoke_test_models.py model=gemma seed=42
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import hydra
import torch
from loguru import logger
from omegaconf import DictConfig, OmegaConf

from persona_rag.evaluation.smoke_suite import SUITE, looks_coherent
from persona_rag.models import (
    GemmaBackend,
    HFBackend,
    HFBackendConfig,
    LlamaBackend,
)

# Stability-gate constants. The peak-memory budget targets a single 32 GB V100.
_PEAK_MEM_BUDGET_GB = 30.0


# --------------------------------------------------------------------------- helpers


def _build_backend(cfg: DictConfig) -> HFBackend:
    model_cfg = HFBackendConfig(
        model_id=cfg.model.model_id,
        name=cfg.model.name,
        revision=cfg.model.revision,
        compute_dtype=cfg.model.compute_dtype,
        attn_implementation=cfg.model.attn_implementation,
        load_in_4bit=cfg.model.load_in_4bit,
        bnb_4bit_quant_type=cfg.model.bnb_4bit_quant_type,
        bnb_4bit_use_double_quant=cfg.model.bnb_4bit_use_double_quant,
        max_input_tokens=cfg.model.max_input_tokens,
        trust_remote_code=cfg.model.trust_remote_code,
        warmup_nan_guard=bool(cfg.model.get("warmup_nan_guard", True)),
    )
    name = cfg.model.name
    if name.startswith("gemma"):
        return GemmaBackend(model_cfg)
    if name.startswith("llama"):
        return LlamaBackend(model_cfg)
    raise ValueError(f"Unknown model name {name!r}")


def _logits_have_nan_or_inf(
    backend: HFBackend, prompt: str, generated: str
) -> tuple[bool, dict[str, float]]:
    """Forward-pass (prompt + generated) and return (bad, stats) via the backend's guard.

    Uses the public :meth:`HFBackend.check_logits_finite` so we share the code path
    with the load-time NaN guard and with the unit tests. ``generated`` is appended to
    ``prompt`` so the forward pass covers both the input and the model's own output.
    """
    ok, stats = backend.check_logits_finite(prompt + generated)
    return (not ok), stats


def _render_prompt(backend: HFBackend, suite_prompt: Any) -> str:
    """Render a suite prompt via the public ``format_persona_prompt`` surface.

    Exercises the Gemma-vs-Llama system-role asymmetry path on every suite
    prompt — not just the ``persona`` bucket — so any bug in the folding logic
    would show up in the 30-prompt run as either a TemplateError (hard fail) or a
    degraded output (caught by the coherence gate).
    """
    return backend.format_persona_prompt(
        system_text=suite_prompt.system,
        user_text=suite_prompt.user,
        history=None,
    )


def _run_stability_suite(
    backend: HFBackend, cfg: DictConfig, report_dir: Path
) -> tuple[list[dict[str, Any]], int, int]:
    """Execute all 30 prompts. Return (results, coherent_count, bad_logits_count)."""
    results: list[dict[str, Any]] = []
    coherent = 0
    bad_logits = 0
    for sp in SUITE:
        rendered = _render_prompt(backend, sp)
        t0 = time.time()
        output = backend.generate(
            rendered,
            max_new_tokens=cfg.max_new_tokens,
            temperature=0.0,
            seed=cfg.seed,
        )
        elapsed = time.time() - t0
        ok, reason = looks_coherent(output)
        if ok:
            coherent += 1

        bad, logit_stats = _logits_have_nan_or_inf(backend, rendered, output)
        if bad:
            bad_logits += 1
            logger.error(
                "NaN/inf in logits on prompt {pid} ({bucket}): {stats}",
                pid=sp.prompt_id,
                bucket=sp.bucket,
                stats=logit_stats,
            )

        results.append(
            {
                "prompt_id": sp.prompt_id,
                "bucket": sp.bucket,
                "system": sp.system,
                "user": sp.user,
                "output": output,
                "coherent": ok,
                "coherence_reason": reason,
                "logits": logit_stats,
                "elapsed_sec": round(elapsed, 3),
            }
        )
        logger.info(
            "[{pid}|{bucket}] coherent={ok} reason={reason} t={t:.2f}s",
            pid=sp.prompt_id,
            bucket=sp.bucket,
            ok=ok,
            reason=reason,
            t=elapsed,
        )

    # Write artifacts.
    (report_dir / "stability_results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (report_dir / "stability_outputs.txt").open("w", encoding="utf-8") as f:
        for r in results:
            f.write(f"=== {r['prompt_id']} [{r['bucket']}]  coherent={r['coherent']} ===\n")
            if r["system"]:
                f.write(f"[SYSTEM] {r['system']}\n")
            f.write(f"[USER]   {r['user']}\n")
            f.write(f"[OUTPUT] {r['output']}\n\n")
    return results, coherent, bad_logits


def _run_reproducibility(backend: HFBackend, cfg: DictConfig, report_dir: Path) -> float:
    """Re-run ``reproducibility_sample_size`` prompts ``reproducibility_runs`` times.

    Returns the fraction that matched token-for-token across ALL repeat runs.
    """
    import random as _random

    rng = _random.Random(cfg.seed)
    sample = rng.sample(list(SUITE), k=cfg.reproducibility_sample_size)

    per_prompt: list[dict[str, Any]] = []
    matched = 0
    for sp in sample:
        rendered = _render_prompt(backend, sp)
        outs: list[str] = [
            backend.generate(
                rendered,
                max_new_tokens=cfg.max_new_tokens,
                temperature=0.0,
                seed=cfg.seed,
            )
            for _ in range(cfg.reproducibility_runs)
        ]
        identical = all(o == outs[0] for o in outs[1:])
        if identical:
            matched += 1
        per_prompt.append(
            {
                "prompt_id": sp.prompt_id,
                "identical_across_runs": identical,
                "outputs": outs,
            }
        )
        logger.info(
            "repro[{pid}] identical={ok}",
            pid=sp.prompt_id,
            ok=identical,
        )

    rate = matched / len(sample)
    payload = {
        "sample_size": cfg.reproducibility_sample_size,
        "runs_per_prompt": cfg.reproducibility_runs,
        "exact_match_rate": rate,
        "hard_min": cfg.reproducibility_hard_min,
        "warn_below": cfg.reproducibility_warn_below,
        "per_prompt": per_prompt,
    }
    (report_dir / "reproducibility.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return rate


def _run_hidden_states_check(backend: HFBackend, report_dir: Path) -> dict[str, Any]:
    """Capture hidden states at probe layers [8, 12, 16, 20].

    These four layers bracket the early-/mid-/late-layer regime that downstream
    persona-vector and geometry work probes. For each layer we exercise all
    three pool modes and report shape so a reader of the smoke report can
    confirm the contract.
    """
    probe_prompts = [
        "Hello, world.",
        "The quick brown fox jumps over the lazy dog.",
        "Testing one two three.",
    ]
    probe_layers = [8, 12, 16, 20]
    # Skip any that exceed this backend's layer count (Llama 3.1 has 32, Gemma has 42;
    # both accommodate 0..20, but we keep the skip path in case of future smaller
    # backbones).
    probe_layers = [idx for idx in probe_layers if idx <= backend.num_layers]

    details: list[dict[str, Any]] = []
    for p in probe_prompts:
        # Exercise all three pool modes with over="prompt" (cheapest, deterministic).
        hs_mean = backend.get_hidden_states(p, layers=probe_layers, pool="mean", over="prompt")
        hs_last = backend.get_hidden_states(p, layers=probe_layers, pool="last", over="prompt")
        hs_none = backend.get_hidden_states(p, layers=probe_layers, pool="none", over="prompt")
        for idx in probe_layers:
            m, last_t, none_t = hs_mean[idx], hs_last[idx], hs_none[idx]
            any_nan = any(bool(torch.isnan(t).any().item()) for t in (m, last_t, none_t))
            any_inf = any(bool(torch.isinf(t).any().item()) for t in (m, last_t, none_t))
            details.append(
                {
                    "prompt": p,
                    "layer": idx,
                    "shape_mean": list(m.shape),
                    "shape_last": list(last_t.shape),
                    "shape_none": list(none_t.shape),
                    "dtype": str(m.dtype),
                    "has_nan": any_nan,
                    "has_inf": any_inf,
                }
            )
    payload = {
        "num_layers": backend.num_layers,
        "hidden_dim": backend.hidden_dim,
        "probed_layers": probe_layers,
        "probes": details,
    }
    (report_dir / "hidden_states_check.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return payload


# --------------------------------------------------------------------------- main


@hydra.main(version_base=None, config_path="../src/persona_rag/config", config_name="smoke_test")
def main(cfg: DictConfig) -> int:
    report_dir = Path(cfg.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stderr, level="INFO", enqueue=False)
    logger.add(report_dir / "smoke_test.log", level="DEBUG", enqueue=False)

    logger.info("Resolved config:\n{c}", c=OmegaConf.to_yaml(cfg))

    if not torch.cuda.is_available():
        logger.error(
            "No CUDA device available. The smoke test does not run on CPU; "
            "run it on the target CUDA host."
        )
        return 1

    backend = _build_backend(cfg)
    backend.save_load_report(report_dir / "load_report.json")

    # --- 30-prompt stability suite
    results, coherent, bad_logits = _run_stability_suite(backend, cfg, report_dir)

    # --- reproducibility
    repro_rate = _run_reproducibility(backend, cfg, report_dir)

    # --- hidden states
    hs_payload = _run_hidden_states_check(backend, report_dir)

    # --- gates
    peak_mem = torch.cuda.max_memory_allocated() / (1024**3)
    gates = {
        "no_nan_or_inf_logits": {
            "passed": bad_logits == 0,
            "detail": f"{bad_logits}/30 prompts had NaN/inf in logits",
        },
        "coherence": {
            "passed": coherent >= cfg.coherence_min_pass,
            "detail": f"coherent={coherent}/30, bar={cfg.coherence_min_pass}",
        },
        "reproducibility": {
            "passed": repro_rate >= cfg.reproducibility_hard_min,
            "detail": f"rate={repro_rate:.0%}, hard_min={cfg.reproducibility_hard_min:.0%}, warn_below={cfg.reproducibility_warn_below:.0%}",
        },
        "peak_memory": {
            "passed": peak_mem < _PEAK_MEM_BUDGET_GB,
            "detail": f"peak={peak_mem:.2f} GB, budget={_PEAK_MEM_BUDGET_GB:.1f} GB",
        },
        "hidden_states_shape": {
            # mean/last must be 1D (hidden_dim,); none must be 2D (seq_len, hidden_dim).
            "passed": all(
                len(p["shape_mean"]) == 1
                and len(p["shape_last"]) == 1
                and len(p["shape_none"]) == 2
                and not (p["has_nan"] or p["has_inf"])
                for p in hs_payload["probes"]
            ),
            "detail": f"probes={len(hs_payload['probes'])}, layers={hs_payload['probed_layers']}",
        },
    }

    summary: dict[str, Any] = {
        "model": asdict(backend._cfg),
        "stability": {
            "coherent": coherent,
            "total": len(results),
            "bad_logits": bad_logits,
        },
        "reproducibility_rate": repro_rate,
        "peak_memory_gb": peak_mem,
        "gates": gates,
    }
    (report_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    all_passed = all(g["passed"] for g in gates.values())
    if all_passed:
        if repro_rate < cfg.reproducibility_warn_below:
            logger.warning(
                "Reproducibility rate {r:.0%} passed the hard-min but is below the "
                "warn threshold {w:.0%}.",
                r=repro_rate,
                w=cfg.reproducibility_warn_below,
            )
        logger.success("Smoke test PASSED on {m}. Report: {d}", m=backend.name, d=report_dir)
        return 0

    logger.error(
        "Smoke test FAILED on {m}. Gates failed: {bad}",
        m=backend.name,
        bad=[k for k, v in gates.items() if not v["passed"]],
    )
    logger.error("Report: {d}", d=report_dir)
    return 1


if __name__ == "__main__":
    sys.exit(main())
