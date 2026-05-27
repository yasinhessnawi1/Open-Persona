"""Render publication-quality figures from saved persona-vector geometry runs.

Reads existing JSON artefacts under ``results/`` and writes PDF/PNG figures
into ``figures/``. No model loading, no GPU. CPU-only matplotlib.

Inputs (must already exist locally — extracted from the server tarball):
    results/drift_trajectory/20260425_155804/raw.json    # Gemma, generation scope, layers 8/12/16/20
    results/drift_trajectory/20260425_163910/raw.json    # Llama, generation scope, layers 6/10/14/18
    results/a11_validation/20260425_151330/per_layer_auroc.json   # Gemma generation scope
    results/a11_validation/20260425_161224/per_layer_auroc.json   # Llama generation scope

Outputs:
    figures/drift_trajectory_lockstep.{pdf,png}
    figures/layer_sweep_heatmap.{pdf,png}
    figures/a3_auroc_table.{pdf,png}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _log(msg: str) -> None:
    print(f"[render] {msg}", flush=True)
    sys.stdout.flush()


_log("importing matplotlib")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

_log("matplotlib ready")


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIG_DIR = ROOT / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)

GEMMA_DRIFT = RESULTS / "drift_trajectory" / "20260425_155804" / "raw.json"
LLAMA_DRIFT = RESULTS / "drift_trajectory" / "20260425_163910" / "raw.json"
GEMMA_AUROC = RESULTS / "a11_validation" / "20260425_151330" / "per_layer_auroc.json"
LLAMA_AUROC = RESULTS / "a11_validation" / "20260425_161224" / "per_layer_auroc.json"

PERSONA_ORDER = ["climate_scientist", "cs_tutor", "historian"]
PERSONA_LABELS = {
    "climate_scientist": "climate scientist",
    "cs_tutor": "cs tutor",
    "historian": "historian",
}

# Colourblind-friendly palette (matplotlib tab10 indices 0, 1)
COLOR_IN = "#0173B2"  # blue — in-persona
COLOR_DRIFT = "#DE8F05"  # orange — drifting


def _set_style() -> None:
    """Apply a clean publication style."""
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(
        {
            "font.size": 11,
            "axes.titlesize": 12,
            "axes.labelsize": 11,
            "legend.fontsize": 10,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "figure.dpi": 110,
            "savefig.dpi": 200,
            "savefig.bbox": "tight",
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def _save(fig: plt.Figure, stem: str) -> None:
    """Save a figure as both PDF (vector, for the report) and PNG (preview)."""
    for ext in ("pdf", "png"):
        path = FIG_DIR / f"{stem}.{ext}"
        fig.savefig(path)
        _log(f"wrote {path}")


def _load(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return json.load(fh)


def figure_lockstep() -> None:
    """Figure 1: per-turn projection traces for both conditions on a single (persona, layer).

    Selection: Gemma generation-scope, cs_tutor at layer 8. The two conditions
    share the first three (in-persona) turns by construction; the drift gradient
    enters at turns 3-5. The visual point is that the two lines remain almost
    indistinguishable across the drift turns — |delta| stays an order of
    magnitude below within-condition turn-to-turn variation.
    """
    raw = _load(GEMMA_DRIFT)
    persona = "cs_tutor"
    layer = "8"
    pp = raw["per_layer"][layer]["per_persona"][persona]
    turns = np.arange(len(pp["in_persona_per_turn"]))
    drift_turns = pp["drift_turns"]
    delta = pp["delta_drift_turns"]

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.axvspan(
        min(drift_turns) - 0.5,
        max(drift_turns) + 0.5,
        alpha=0.16,
        color="#9aa0a6",
        zorder=0,
        label="drift-gradient turns",
    )
    ax.plot(
        turns,
        pp["in_persona_per_turn"],
        marker="o",
        linewidth=2.2,
        color=COLOR_IN,
        label="in-persona conversation",
    )
    ax.plot(
        turns,
        pp["drifting_per_turn"],
        marker="s",
        linewidth=2.2,
        linestyle="--",
        color=COLOR_DRIFT,
        label="drifting conversation",
    )
    ax.axhline(0.0, color="black", linewidth=0.6, alpha=0.4)
    ax.set_xlabel("turn index")
    ax.set_ylabel("persona-vector projection")
    ax.set_xticks(turns)
    ax.set_title("Persona-vector projection does not differentiate drift conditions")
    ax.legend(loc="upper left", frameon=False)

    backend = raw["backend"]["name"]
    annot = (
        f"{backend}  ·  layer {layer}  ·  {PERSONA_LABELS[persona]}\n"
        f"|delta| over drift turns = {abs(delta):.3f}"
    )
    ax.text(
        0.98,
        0.04,
        annot,
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=9,
        color="#444",
    )

    _save(fig, "drift_trajectory_lockstep")
    plt.close(fig)


def _delta_matrix(raw: dict[str, Any]) -> tuple[np.ndarray, list[int], list[str]]:
    """Return ``|delta_drift_turns|`` as a (layers x personas) matrix."""
    layers = sorted((int(k) for k in raw["per_layer"]), reverse=False)
    mat = np.zeros((len(layers), len(PERSONA_ORDER)))
    for i, ly in enumerate(layers):
        per_p = raw["per_layer"][str(ly)]["per_persona"]
        for j, p in enumerate(PERSONA_ORDER):
            mat[i, j] = abs(per_p[p]["delta_drift_turns"])
    return mat, layers, PERSONA_ORDER


def figure_layer_sweep() -> None:
    """Figure 2: heatmap of |delta_drift_turns| across layers x personas.

    Two panels side by side, one per model. The thresholds 0.10 (refute floor)
    and 0.30 (proceed floor) are highlighted on the colour bar so the reader
    can see at a glance that every cell falls well below both.
    """
    gemma = _load(GEMMA_DRIFT)
    llama = _load(LLAMA_DRIFT)
    g_mat, g_layers, _ = _delta_matrix(gemma)
    l_mat, l_layers, _ = _delta_matrix(llama)
    vmax = 0.15
    # A near-white-to-deep colourmap that stays light at small values
    cmap = LinearSegmentedColormap.from_list(
        "delta", ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"]
    )

    fig = plt.figure(figsize=(11.0, 4.4))
    # 2 heatmap panels + 1 narrow colour-bar column, with explicit width ratios
    # so the bar never overlaps the right panel.
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.0, 0.05], wspace=0.35)
    ax_g = fig.add_subplot(gs[0, 0])
    ax_l = fig.add_subplot(gs[0, 1])
    cax = fig.add_subplot(gs[0, 2])
    panels = (
        (ax_g, g_mat, g_layers, "gemma2-9b-it"),
        (ax_l, l_mat, l_layers, "llama3.1-8b-instruct"),
    )
    im = None
    for ax, mat, layers, title in panels:
        im = ax.imshow(mat, cmap=cmap, vmin=0.0, vmax=vmax, aspect="auto")
        ax.set_xticks(range(len(PERSONA_ORDER)))
        ax.set_xticklabels([PERSONA_LABELS[p] for p in PERSONA_ORDER], rotation=20, ha="right")
        ax.set_yticks(range(len(layers)))
        ax.set_yticklabels([f"L{ly}" for ly in layers])
        ax.set_title(title)
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                ax.text(
                    j,
                    i,
                    f"{mat[i, j]:.3f}",
                    ha="center",
                    va="center",
                    color="#222",
                    fontsize=9,
                )
    ax_g.set_ylabel("layer")
    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label("|projection delta| over drift turns")
    # The refute floor (0.10) sits inside the colour-bar range; the proceed
    # floor (0.30) is well above vmax=0.15 and is left to the caption.
    cbar.ax.axhline(0.10, color="#444", linewidth=1.0)
    fig.suptitle(
        "Projection delta between conditions stays below the refute floor (0.10) at every layer",
        y=0.99,
    )
    fig.subplots_adjust(top=0.86, bottom=0.20, left=0.07, right=0.93)

    _save(fig, "layer_sweep_heatmap")
    plt.close(fig)


def _auroc_rows(
    report: dict[str, Any],
) -> tuple[list[str], list[int], np.ndarray, dict[str, dict[str, float]]]:
    """Return (personas, layers, auroc_matrix, controls)."""
    cfg = report["config"]
    layers = list(cfg["layers"])
    personas = PERSONA_ORDER
    mat = np.zeros((len(layers), len(personas)))
    controls: dict[str, dict[str, float]] = {}
    for j, p in enumerate(personas):
        pp = report["per_persona"][p]
        per_layer = pp["per_layer_auroc"]
        for i, ly in enumerate(layers):
            mat[i, j] = per_layer[str(ly)]
        controls[p] = {
            "shuffled_label_max": max(
                pp.get("shuffled_label_auroc", {}).values() or [float("nan")]
            ),
            "random_feature": pp.get("random_feature_auroc", float("nan")),
        }
    return personas, layers, mat, controls


def _draw_auroc_panel(ax: plt.Axes, report: dict[str, Any], title: str) -> None:
    personas, layers, mat, controls = _auroc_rows(report)
    n_rows = len(layers) + 2  # + shuffled-label + random-feature controls
    n_cols = len(personas)

    # Display matrix with controls appended at the bottom
    ctrl_shuf = np.array([controls[p]["shuffled_label_max"] for p in personas])
    ctrl_rand = np.array([controls[p]["random_feature"] for p in personas])
    full = np.vstack([mat, ctrl_shuf[None, :], ctrl_rand[None, :]])

    # Two-zone colourmap: confirmed AUROCs use a single dark band, controls
    # use a contrasting muted band so they don't draw the eye away.
    cmap = LinearSegmentedColormap.from_list(
        "auroc", ["#ffffff", "#deebf7", "#9ecae1", "#3182bd", "#08519c"]
    )
    ax.imshow(full, cmap=cmap, vmin=0.4, vmax=1.0, aspect="auto")

    ax.set_xticks(range(n_cols))
    ax.set_xticklabels([PERSONA_LABELS[p] for p in personas], rotation=20, ha="right")
    ytick_labels = [f"L{ly}" for ly in layers] + ["shuffled-label", "random-feature"]
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(ytick_labels)
    ax.set_title(title)

    for i in range(n_rows):
        for j in range(n_cols):
            val = full[i, j]
            color = "white" if val >= 0.85 else "#222"
            ax.text(j, i, f"{val:.2f}", ha="center", va="center", color=color, fontsize=9)

    # Horizontal rule between AUROC rows and control rows
    ax.axhline(len(layers) - 0.5, color="#333", linewidth=1.2)


def figure_auroc_table() -> None:
    """Figure 3: AUROC table for both models, with Dubanowska controls below."""
    gemma = _load(GEMMA_AUROC)
    llama = _load(LLAMA_AUROC)
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.6))
    _draw_auroc_panel(axes[0], gemma, "gemma2-9b-it  ·  generation scope")
    _draw_auroc_panel(axes[1], llama, "llama3.1-8b-instruct  ·  generation scope")
    axes[0].set_ylabel("layer  /  control")
    fig.suptitle(
        "Per-layer AUROC for persona classifiers, with shuffled-label and random-feature controls",
        y=1.02,
    )
    fig.tight_layout()
    _save(fig, "a3_auroc_table")
    plt.close(fig)


def main() -> None:
    _log("style")
    _set_style()
    _log("figure_lockstep")
    figure_lockstep()
    _log("figure_layer_sweep")
    figure_layer_sweep()
    _log("figure_auroc_table")
    figure_auroc_table()
    _log("done")


if __name__ == "__main__":
    main()
