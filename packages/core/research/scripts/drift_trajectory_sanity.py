"""Drift-trajectory sanity experiment.

For every persona under ``conversations_dir`` and each of two conditions
(``in_persona``, ``drifting``), runs one forward pass per turn — projecting
the prompt-time hidden state at every requested layer onto the persona's
cached persona vector — and records per-turn drift trajectories. Produces:

    results/drift_trajectory/<run_id>/
        report.md                       — verdict + per-layer breakdown
        raw.json                        — per-turn drift values + metadata
        trajectories_layer<N>.png       — one figure per layer
        verify.log                      — full debug log

Uses the cached persona vectors written by the validation script
(``cache_dir`` config) — those files are bit-exact via safetensors.

Usage:

    # Single layer (original behaviour).
    uv run python scripts/drift_trajectory_sanity.py model=gemma layer=8

    # Layer sweep over the cached layer set.
    uv run python scripts/drift_trajectory_sanity.py model=gemma 'layer=[8,12,16,20]'

    # Point at a non-default cache (e.g. a generation-scope cache built by
    # `validate_persona_vectors.py extraction_scope=generation \\
    #  vectors_cache_dir=./.chroma/persona_vectors_generation`).
    uv run python scripts/drift_trajectory_sanity.py model=gemma \\
        cache_dir=./.chroma/persona_vectors_generation 'layer=[8,12,16,20]'

Generation-scope flow (when prompt-scope is inconclusive):

    1. Re-run validation with generation-scope, into a separate cache dir:
       uv run python scripts/validate_persona_vectors.py model=gemma \\
           extraction_pool=mean extraction_scope=generation \\
           vectors_cache_dir=./.chroma/persona_vectors_generation
    2. Re-run this script pointed at that cache:
       uv run python scripts/drift_trajectory_sanity.py model=gemma \\
           cache_dir=./.chroma/persona_vectors_generation 'layer=[8,12,16,20]'

    Note: this experiment still measures drift on prompt-scope hidden states
    (we don't have a generation at measurement time — the assistant turn
    is hand-authored). The generation-scope re-extraction changes the
    persona-vector *direction* the projection is taken against, not the
    measurement protocol. The hypothesis being tested: a direction trained
    on generation-scope activations may discriminate hand-authored
    in_persona vs drifting content better than a prompt-scope direction
    does.

Notes:

- The script imports from ``persona_rag.models``; on macOS this fails because
  ``bitsandbytes`` is unavailable. Run on a CUDA host.
- The persona-vector cache must already exist (run the validation script
  first).
- Each persona is rendered into the system prompt as identity + constraints
  + self_facts + worldview, assembled into a single block.
- A multi-layer sweep costs the same forward-pass time as a single layer
  (one forward pass captures all requested layers simultaneously).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hydra
import numpy as np
from loguru import logger
from omegaconf import DictConfig, OmegaConf

from persona_rag.schema.conversation import (
    DriftTrajectoryConversation,
    assert_user_turns_match,
)
from persona_rag.schema.persona import Persona
from persona_rag.vectors.cache import load_persona_vectors
from persona_rag.vectors.drift import DriftSignal

REPO_ROOT = Path(__file__).resolve().parents[1]
PERSONAS_DIR = REPO_ROOT / "personas"


# --------------------------------------------------------------------- helpers


def _load_backend(cfg: DictConfig) -> Any:
    """Load the LLM backend. Mirrors scripts/validate_persona_vectors.py."""
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


def _persona_system_text(persona: Persona) -> str:
    """Render the persona as a system-style block.

    Composition: identity + constraints + self_facts + worldview, assembled
    into a single string. This is the canonical assembly used by both this
    experiment and any downstream retrieval mechanism that needs a stable
    persona prompt prefix.
    """
    parts: list[str] = []
    parts.append(f"You are {persona.identity.name}, {persona.identity.role}.")
    parts.append(f"Background: {persona.identity.background}".strip())
    if persona.identity.constraints:
        parts.append("Constraints:")
        parts.extend(f"- {c}" for c in persona.identity.constraints)
    if persona.self_facts:
        parts.append("Self-facts:")
        parts.extend(f"- {f.fact}" for f in persona.self_facts)
    if persona.worldview:
        parts.append("Worldview claims (with epistemic tags):")
        parts.extend(f"- ({w.epistemic}) {w.claim}" for w in persona.worldview)
    parts.append(
        "Respond as this persona would. Use the self-facts and worldview as your "
        "own. Respect every constraint."
    )
    return "\n".join(parts)


@dataclass
class TurnReading:
    persona_id: str
    condition: str
    turn_idx: int  # zero-based pair index (0..n_pairs-1)
    layer: int  # transformer layer the drift was measured at
    user_text: str
    assistant_text: str
    drift_level: str | None  # only on drifting condition
    drift: float


def _measure_turn_drift(
    *,
    backend: Any,
    persona_system: str,
    conv: DriftTrajectoryConversation,
    drift_signals: dict[int, DriftSignal],
) -> list[TurnReading]:
    """Per-turn, per-layer pre-generation drift measurement.

    DESIGN CHOICE — history threading:
        At turn t, the prompt's history contains turns 0..t-1 of THIS
        conversation's assistant content. So the drifting condition's
        prompt at turn 5 contains the drifting assistant turns 1-4 in
        history; the in-persona condition's prompt at turn 5 contains
        the in-persona assistant turns 1-4.

        This matches the runtime semantics of a drift-gated retrieval
        mechanism: the drift detector at turn t sees the conversation as
        actually generated, not as it would have been in an in-persona run.
        An alternative design (always thread in-persona history regardless
        of condition) would isolate single-turn drift detection — but that's
        not what runtime gating does, so it's not what we test here.

        Consequence: drift measurement at turn t consumes assistant turns
        0..t-1 as history. With the shipped YAMLs, drifting assistant
        content first diverges at turn 2 (the first ``subtle`` annotation),
        so drift *measurements* first diverge at turn 3 — i.e. measurement
        at turns 0, 1, 2 is guaranteed identical between conditions; the
        drift-turns delta averages turns 3..n_pairs-1.

    For each pair index t (0..n_pairs-1):
      - Build the prompt: system + persona block + history through turn t-1
        + the user message for turn t. (We measure BEFORE the assistant turn
        t, so the hidden state captures what the model believes the next
        response should look like, not the response itself.)
      - Run ONE forward pass that captures all requested layers
        simultaneously; project each layer's last-token hidden state onto
        its matching :class:`DriftSignal`. Multi-layer sweeps cost the same
        forward-pass time as a single-layer run.
    """
    from persona_rag.models.base import ChatMessage

    layers = sorted(drift_signals.keys())
    user_texts = conv.user_turn_texts()
    assistant_texts = conv.assistant_turn_texts()

    readings: list[TurnReading] = []
    for t in range(conv.n_pairs):
        history: list[ChatMessage] = []
        for prior in range(t):
            history.append(ChatMessage(role="user", content=user_texts[prior]))
            history.append(ChatMessage(role="assistant", content=assistant_texts[prior]))

        prompt = backend.format_persona_prompt(
            system_text=persona_system,
            user_text=user_texts[t],
            history=history if history else None,
        )

        captured = backend.get_hidden_states(
            prompt,
            layers=layers,
            pool="last",
            over="prompt",
        )

        drift_level: str | None = None
        if conv.condition == "drifting":
            asst_turns = [tt for tt in conv.turns if tt.role == "assistant"]
            drift_level = asst_turns[t].drift_level

        per_layer_drifts: dict[int, float] = {}
        for layer in layers:
            h = captured[layer].detach().to("cpu").float().squeeze()
            if h.dim() != 1:
                raise RuntimeError(
                    f"unexpected hidden-state shape {tuple(h.shape)} at layer {layer}; "
                    "expected 1-D after pool='last' over='prompt'"
                )
            d = float(drift_signals[layer].compute(h))
            per_layer_drifts[layer] = d
            readings.append(
                TurnReading(
                    persona_id=conv.persona_id,
                    condition=conv.condition,
                    turn_idx=t,
                    layer=layer,
                    user_text=user_texts[t][:120],
                    assistant_text=assistant_texts[t][:120],
                    drift_level=drift_level,
                    drift=d,
                )
            )
        logger.info(
            "{}/{} turn={} authored={} drifts={}",
            conv.persona_id,
            conv.condition,
            t,
            drift_level or "-",
            {layer: round(per_layer_drifts[layer], 3) for layer in layers},
        )
    return readings


# --------------------------------------------------------------------- analysis


def _decide_verdict(
    per_persona_delta: dict[str, float],
    *,
    delta_h1: float,
    delta_h2: float,
) -> tuple[str, str]:
    """Return (verdict, rationale) for one of three outcomes.

    Delta is signed: positive means in_persona reads higher than drifting
    (the expected direction for a working persona-fidelity signal). The
    verdict thresholds are interpreted directionally:

    - ``proceed``: positive delta >= delta_h1 on >= 2 of N personas — i.e.
      most personas show an in_persona > drifting differential of the
      expected magnitude.
    - ``refuted``: |delta| < delta_h2 on ALL personas — magnitudes are
      uniformly small in both directions; the signal does not reliably
      discriminate. This branch does NOT claim "the signal is constant"
      (it may move within each condition); it claims the means do not
      separate at the threshold magnitude.
    - ``inconclusive``: anything else — including the case where some
      personas show wrong-direction (negative) deltas larger than
      ``delta_h2``, which is a real finding requiring human review rather
      than either "proceed" or "refuted".
    """
    n = len(per_persona_delta)
    deltas = list(per_persona_delta.values())
    n_strong_correct = sum(1 for d in deltas if d >= delta_h1)
    n_small_magnitude = sum(1 for d in deltas if abs(d) < delta_h2)
    n_wrong_dir = sum(1 for d in deltas if d <= -delta_h2)

    if n_strong_correct >= max(2, n - 1):
        return "proceed", (
            f"{n_strong_correct}/{n} personas show delta >= {delta_h1:.2f} "
            "(in_persona reads higher than drifting at the expected magnitude)."
        )

    if n_small_magnitude == n:
        return "refuted", (
            f"all {n} personas show |delta| < {delta_h2:.2f}; per-condition means do not "
            "separate at the threshold magnitude. Per-turn trajectories may still move; "
            "what fails here is the directional in_persona vs drifting differential, not "
            "signal stability."
        )

    return "inconclusive", (
        f"{n_strong_correct}/{n} personas above the proceed threshold; "
        f"{n_small_magnitude}/{n} below the small-magnitude floor; "
        f"{n_wrong_dir}/{n} show wrong-direction (negative) deltas of magnitude "
        f">= {delta_h2:.2f}. The per-turn trajectories should be reviewed before "
        "concluding what the signal does or does not measure."
    )


def _trajectory_figure(
    readings: list[TurnReading],
    out_path: Path,
    *,
    layer: int,
) -> None:
    """One subplot per persona: drift x turn for both conditions, at one layer."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_persona: dict[str, dict[str, list[tuple[int, float]]]] = {}
    for r in readings:
        if r.layer != layer:
            continue
        by_persona.setdefault(r.persona_id, {}).setdefault(r.condition, []).append(
            (r.turn_idx, r.drift)
        )

    persona_ids = sorted(by_persona.keys())
    fig, axes = plt.subplots(
        1, len(persona_ids), figsize=(5 * len(persona_ids), 4), sharey=True, squeeze=False
    )
    for ax, pid in zip(axes[0], persona_ids, strict=True):
        for cond, marker, color in (
            ("in_persona", "o", "C0"),
            ("drifting", "x", "C3"),
        ):
            pts = sorted(by_persona[pid].get(cond, []))
            if not pts:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            ax.plot(xs, ys, marker=marker, color=color, label=cond, linewidth=1.5)
        ax.axhline(0.0, linestyle=":", color="grey", linewidth=0.7)
        ax.set_ylim(-1.05, 1.05)
        ax.set_xlabel("turn index")
        ax.set_title(pid)
        ax.legend(loc="lower left", fontsize=8)
    axes[0][0].set_ylabel("drift signal (cosine, [-1, +1])")
    fig.suptitle(f"Drift trajectory @ layer {layer} — per turn, by persona and condition")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _summarise_layer(
    readings_at_layer: list[TurnReading],
    n_pairs: int,
) -> dict[str, dict[str, Any]]:
    """Compute per-persona delta summary for one layer's readings."""
    drift_turns = tuple(range(3, n_pairs))
    out: dict[str, dict[str, Any]] = {}

    by_persona_cond: dict[str, dict[str, list[TurnReading]]] = {}
    for r in readings_at_layer:
        by_persona_cond.setdefault(r.persona_id, {}).setdefault(r.condition, []).append(r)

    for pid, by_cond in by_persona_cond.items():
        in_readings = sorted(by_cond.get("in_persona", []), key=lambda r: r.turn_idx)
        drift_readings = sorted(by_cond.get("drifting", []), key=lambda r: r.turn_idx)

        in_all = np.array([r.drift for r in in_readings])
        drift_all = np.array([r.drift for r in drift_readings])
        mu_in_all = float(in_all.mean())
        mu_drift_all = float(drift_all.mean())

        in_dt = np.array([r.drift for r in in_readings if r.turn_idx in drift_turns])
        drift_dt = np.array([r.drift for r in drift_readings if r.turn_idx in drift_turns])
        mu_in_dt = float(in_dt.mean()) if in_dt.size else float("nan")
        mu_drift_dt = float(drift_dt.mean()) if drift_dt.size else float("nan")

        out[pid] = {
            "mu_in_all_turns": mu_in_all,
            "mu_drift_all_turns": mu_drift_all,
            "delta_all_turns": mu_in_all - mu_drift_all,
            "mu_in_drift_turns": mu_in_dt,
            "mu_drift_drift_turns": mu_drift_dt,
            "delta_drift_turns": mu_in_dt - mu_drift_dt,
            "drift_turns": list(drift_turns),
            "in_persona_per_turn": [r.drift for r in in_readings],
            "drifting_per_turn": [r.drift for r in drift_readings],
            "drift_levels_drifting": [r.drift_level for r in drift_readings],
        }
    return out


def _row_label(d_dt: float, *, h1: float, h2: float) -> str:
    """Per-persona row label for the report table."""
    if d_dt >= h1:
        return "proceed"
    if abs(d_dt) < h2:
        return "small"
    if d_dt <= -h2:
        return "wrong-direction"
    return "inconclusive"


def _coerce_layers(value: Any) -> list[int]:
    """Accept either a single int or a list of ints from the Hydra config."""
    if isinstance(value, int):
        return [value]
    layers = list(value)
    if not layers:
        raise ValueError("`layer` must be a non-empty int or list of ints")
    if not all(isinstance(L, int) for L in layers):
        raise ValueError(f"`layer` list must contain ints, got {layers!r}")
    return sorted(set(layers))


# --------------------------------------------------------------------- main


@hydra.main(
    config_path="../src/persona_rag/config", config_name="drift_trajectory", version_base=None
)
def main(cfg: DictConfig) -> int:
    """Run the drift-trajectory experiment end-to-end."""
    report_dir = Path(cfg.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    logger.add(report_dir / "verify.log", level="DEBUG")
    logger.info("drift-trajectory: config\n{}", OmegaConf.to_yaml(cfg))
    (report_dir / "config.yaml").write_text(OmegaConf.to_yaml(cfg), encoding="utf-8")

    layers = _coerce_layers(cfg.layer)
    cache_dir = Path(cfg.cache_dir)
    convs_dir = Path(cfg.conversations_dir)
    h1 = float(cfg.delta_h1)
    h2 = float(cfg.delta_h2)

    if not cache_dir.exists():
        logger.error(
            "persona-vector cache_dir does not exist: {} — run "
            "scripts/validate_persona_vectors.py first",
            cache_dir,
        )
        return 2

    # Discover personas via the conversation directory layout.
    persona_dirs = sorted(p for p in convs_dir.iterdir() if p.is_dir())
    if not persona_dirs:
        logger.error("no persona conversations under {}", convs_dir)
        return 2
    logger.info("personas to evaluate: {}", [p.name for p in persona_dirs])
    logger.info("layers to evaluate: {}", layers)

    backend = _load_backend(cfg)

    all_readings: list[TurnReading] = []
    n_pairs_seen: int | None = None

    for persona_dir in persona_dirs:
        pid = persona_dir.name

        persona_yaml = PERSONAS_DIR / f"{pid}.yaml"
        if not persona_yaml.exists():
            logger.error("persona {!r}: yaml not found at {}", pid, persona_yaml)
            return 2
        persona = Persona.from_yaml(persona_yaml)

        # Cached persona vectors → one DriftSignal per requested layer.
        try:
            pv = load_persona_vectors(cache_dir, pid)
        except FileNotFoundError as err:
            logger.error("persona {!r}: cache miss — {}", pid, err)
            return 2
        missing = [L for L in layers if L not in pv.in_persona_centroid]
        if missing:
            logger.error(
                "persona {!r}: layer(s) {} not in cached vectors (have {})",
                pid,
                missing,
                sorted(pv.in_persona_centroid),
            )
            return 2
        drift_signals = {L: DriftSignal.from_persona_vectors(pv, L) for L in layers}

        in_conv = DriftTrajectoryConversation.from_yaml(persona_dir / "in_persona.yaml")
        drift_conv = DriftTrajectoryConversation.from_yaml(persona_dir / "drifting.yaml")
        assert_user_turns_match([in_conv, drift_conv])
        if n_pairs_seen is None:
            n_pairs_seen = in_conv.n_pairs
        elif n_pairs_seen != in_conv.n_pairs:
            logger.error(
                "persona {!r} has n_pairs={} but a previous persona had {}; "
                "all conversations must share n_pairs.",
                pid,
                in_conv.n_pairs,
                n_pairs_seen,
            )
            return 2

        persona_system = _persona_system_text(persona)

        in_readings = _measure_turn_drift(
            backend=backend,
            persona_system=persona_system,
            conv=in_conv,
            drift_signals=drift_signals,
        )
        drift_readings = _measure_turn_drift(
            backend=backend,
            persona_system=persona_system,
            conv=drift_conv,
            drift_signals=drift_signals,
        )
        all_readings.extend(in_readings)
        all_readings.extend(drift_readings)

    assert n_pairs_seen is not None  # personas non-empty by guard above

    # Per-layer summarisation + verdict.
    per_layer_summary: dict[int, dict[str, dict[str, Any]]] = {}
    per_layer_delta: dict[int, dict[str, float]] = {}
    per_layer_verdict: dict[int, dict[str, str]] = {}
    for layer in layers:
        readings_at_layer = [r for r in all_readings if r.layer == layer]
        summary = _summarise_layer(readings_at_layer, n_pairs=n_pairs_seen)
        per_layer_summary[layer] = summary
        deltas = {pid: s["delta_drift_turns"] for pid, s in summary.items()}
        per_layer_delta[layer] = deltas
        verdict, rationale = _decide_verdict(deltas, delta_h1=h1, delta_h2=h2)
        per_layer_verdict[layer] = {"verdict": verdict, "rationale": rationale}
        for pid, d in deltas.items():
            logger.info(
                "layer={} persona={!r}: delta_drift_turns={:+.3f}",
                layer,
                pid,
                d,
            )
        logger.info("layer={} → verdict={} ({})", layer, verdict, rationale)

        _trajectory_figure(
            all_readings,
            report_dir / f"trajectories_layer{layer}.png",
            layer=layer,
        )

    # Overall verdict: best layer is the one with the largest mean delta_drift_turns
    # across personas (positive direction). If none reaches "proceed", report the
    # best-layer verdict so the headline reflects the best case.
    layer_mean_delta = {
        layer: float(np.mean(list(per_layer_delta[layer].values()))) for layer in layers
    }
    best_layer = max(layers, key=lambda layer: layer_mean_delta[layer])
    overall_verdict = per_layer_verdict[best_layer]["verdict"]
    overall_rationale = per_layer_verdict[best_layer]["rationale"]
    if len(layers) > 1:
        overall_rationale = (
            f"best layer over the sweep is layer {best_layer} "
            f"(mean delta {layer_mean_delta[best_layer]:+.3f}). "
            f"At that layer: {overall_rationale}"
        )

    # Single-layer back-compat for downstream consumers of raw.json that read
    # the flat `per_persona*` fields. When a sweep is run, these expose the
    # best-layer numbers; the per-layer breakdown lives under `per_layer`.
    flat_per_persona = per_layer_summary[best_layer]
    flat_per_persona_delta = per_layer_delta[best_layer]

    raw = {
        "script": "scripts/drift_trajectory_sanity.py",
        "backend": {
            "name": getattr(backend, "name", "?"),
            "model_id": getattr(backend, "model_id", "?"),
            "num_layers": getattr(backend, "num_layers", None),
            "hidden_dim": getattr(backend, "hidden_dim", None),
        },
        "config": {
            "layers": layers,
            "best_layer": best_layer,
            "cache_dir": str(cache_dir),
            "delta_h1": h1,
            "delta_h2": h2,
        },
        # Per-layer breakdown (the sweep deliverable).
        "per_layer": {
            str(L): {
                "verdict": per_layer_verdict[L]["verdict"],
                "rationale": per_layer_verdict[L]["rationale"],
                "mean_delta": layer_mean_delta[L],
                "per_persona": per_layer_summary[L],
                "per_persona_delta": per_layer_delta[L],
            }
            for L in layers
        },
        # Flat best-layer fields for callers that don't iterate per_layer.
        "per_persona": flat_per_persona,
        "per_persona_delta": flat_per_persona_delta,
        "per_persona_delta_kind": "delta_drift_turns",
        "verdict": overall_verdict,
        "rationale": overall_rationale,
        "readings": [
            {
                "persona_id": r.persona_id,
                "condition": r.condition,
                "turn_idx": r.turn_idx,
                "layer": r.layer,
                "user_text": r.user_text,
                "assistant_text": r.assistant_text,
                "drift_level": r.drift_level,
                "drift": r.drift,
            }
            for r in all_readings
        ],
    }
    (report_dir / "raw.json").write_text(
        json.dumps(raw, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )

    # report.md
    lines: list[str] = []
    lines.append(f"# Drift Trajectory — Verdict: **{overall_verdict}**")
    lines.append("")
    lines.append(f"- Backend: `{raw['backend']['name']}` ({raw['backend']['model_id']})")
    lines.append(f"- Layers swept: {layers}")
    if len(layers) > 1:
        lines.append(f"- Best layer (max mean delta_drift_turns): **{best_layer}**")
    lines.append(f"- Cache dir: `{cache_dir}`")
    lines.append(f"- Thresholds: proceed >= {h1}; refuted < {h2}")
    lines.append("")
    lines.append(f"**Rationale:** {overall_rationale}")
    lines.append("")
    lines.append(
        "**Design note:** Per-turn measurements thread each condition's own "
        "assistant content into history. The drifting condition's prompt at "
        "turn t contains the drifting assistant turns 0..t-1; the in-persona "
        "condition's prompt at turn t contains the in-persona assistant turns "
        "0..t-1. This matches the runtime semantics of a drift-gated retrieval "
        "mechanism — the drift detector sees the conversation as actually "
        "generated. Consequence: drift measurement at turn t depends on "
        "history 0..t-1; with the shipped YAMLs the first authored drift sits "
        "in assistant turn 2 (`subtle`), so drift *measurements* first diverge "
        "at turn 3. Turns 0-2 produce identical readings between conditions "
        "by design."
    )
    lines.append("")

    # If we ran a sweep, lead with a layer-summary table that shows verdicts at a glance.
    if len(layers) > 1:
        lines.append("## Layer sweep summary")
        lines.append("")
        lines.append("| Layer | mean delta (drift turns) | per-persona deltas | verdict |")
        lines.append("|---|---|---|---|")
        for layer in layers:
            deltas = per_layer_delta[layer]
            mean_d = layer_mean_delta[layer]
            v = per_layer_verdict[layer]["verdict"]
            per_persona_str = ", ".join(f"{pid[:12]}={d:+.3f}" for pid, d in deltas.items())
            mark = "**" if layer == best_layer else ""
            lines.append(f"| {mark}{layer}{mark} | {mean_d:+.3f} | {per_persona_str} | {v} |")
        lines.append("")

    # Per-layer detail.
    for layer in layers:
        layer_summary = per_layer_summary[layer]
        v = per_layer_verdict[layer]["verdict"]
        r = per_layer_verdict[layer]["rationale"]

        if len(layers) > 1:
            lines.append(f"## Layer {layer} — verdict: **{v}**")
        else:
            lines.append("## Per-persona deltas")
        lines.append("")
        if len(layers) > 1:
            lines.append(f"**Rationale (layer {layer}):** {r}")
            lines.append("")

        lines.append(
            "| Persona | mu_in (all) | mu_drift (all) | delta (all) | "
            "delta (drift turns) | per-persona verdict |"
        )
        lines.append("|---|---|---|---|---|---|")
        for pid, summary in layer_summary.items():
            d_dt = summary["delta_drift_turns"]
            row_label = _row_label(d_dt, h1=h1, h2=h2)
            lines.append(
                "| `{pid}` | {mi:+.3f} | {md:+.3f} | {dla:+.3f} | **{dldt:+.3f}** | {v} |".format(
                    pid=pid,
                    mi=summary["mu_in_all_turns"],
                    md=summary["mu_drift_all_turns"],
                    dla=summary["delta_all_turns"],
                    dldt=d_dt,
                    v=row_label,
                )
            )
        lines.append("")
        lines.append(
            "**Note:** the verdict uses `delta (drift turns)` — averaged over "
            "turns 3..n_pairs-1, where drift measurements actually diverge "
            "between conditions. The `delta (all)` column is shown for "
            "disclosure; it dilutes the signal because turns 0-2 are guaranteed "
            "identical across conditions by experimental design."
        )
        lines.append("")
        lines.append(f"### Per-turn drift trajectories @ layer {layer}")
        lines.append("")
        for pid, summary in layer_summary.items():
            lines.append(f"#### {pid}")
            lines.append("")
            lines.append("| turn | in_persona drift | drifting drift | authored drift_level |")
            lines.append("|---|---|---|---|")
            for t, (di, dd) in enumerate(
                zip(summary["in_persona_per_turn"], summary["drifting_per_turn"], strict=True)
            ):
                lvl = summary["drift_levels_drifting"][t] or "-"
                lines.append(f"| {t} | {di:+.3f} | {dd:+.3f} | {lvl} |")
            lines.append("")
        lines.append(f"![drift trajectories @ layer {layer}](trajectories_layer{layer}.png)")
        lines.append("")

    lines.append("## Decision rule")
    lines.append("")
    lines.append(
        "Delta is signed: positive means in_persona reads higher than drifting "
        "(the expected direction). Verdicts:"
    )
    lines.append("")
    lines.append(
        f"- **proceed**: delta >= {h1} on >= 2 of N personas (correct "
        "direction at threshold magnitude)."
    )
    lines.append(
        f"- **refuted**: |delta| < {h2} on ALL personas — per-condition means "
        "do not separate at the threshold magnitude in either direction."
    )
    lines.append(
        f"- **inconclusive**: anything else, including the case where one or more "
        f"personas show wrong-direction (negative) deltas of magnitude >= "
        f"{h2}. Per-persona row labels (`small` / `wrong-direction` / "
        "`proceed`) show which kind of below-threshold each persona is."
    )
    (report_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info(
        "drift-trajectory complete: best_layer={} verdict={}",
        best_layer,
        overall_verdict,
    )
    # Exit codes: 0 on proceed, 1 on inconclusive, 2 on refuted.
    if overall_verdict == "proceed":
        return 0
    if overall_verdict == "inconclusive":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
