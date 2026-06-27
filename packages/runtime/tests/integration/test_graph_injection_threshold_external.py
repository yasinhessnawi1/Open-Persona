"""K3 injection-threshold validation — real bge-small sweep (D-K3-3 / K3-R-3).

The load-bearing validation: ``inject_similarity_floor`` ships **validated, not
inherited**. This embeds the committed labelled relevant-vs-small-talk set with
the real ``bge-small-en-v1.5`` embedder, takes cosine similarity as the injection
score, and sweeps the floor for the F0.5-precision-biased operating point
(stuffing degrades every turn → precision weighted 2× over recall, the K0 house
posture). It asserts the SHIPPED default is a sound point (high precision = no
stuffing; acceptable recall = not starving) and prints the full table as the
evidence captured in ``docs/specs/phase3/spec_K3/evidence/``.

``@pytest.mark.external`` (loads a real model, ~3-5s) so it is out of the default
CI run; no API key needed — the embedder is local.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from persona.graph.calibration import best_threshold, sweep_thresholds
from persona.graph.config import GraphSettings
from persona.stores.embedder import SentenceTransformerEmbedder

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "graph_injection_threshold.yaml"

pytestmark = pytest.mark.external


def _cosine(a: list[float], b: list[float]) -> float:
    # Embeddings are L2-normalised (normalize=True) → cosine == dot product.
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_inject_similarity_floor_is_validated_against_the_injection_task() -> None:
    entries = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    embedder = SentenceTransformerEmbedder()

    queries = embedder.encode([e["query"] for e in entries])
    contents = embedder.encode([e["content"] for e in entries])
    scored_labels = [
        (_cosine(q, c), bool(e["inject"]))
        for q, c, e in zip(queries, contents, entries, strict=True)
    ]

    floors = [round(0.55 + 0.01 * i, 2) for i in range(36)]  # 0.55 .. 0.90
    results = sweep_thresholds(scored_labels, thresholds=floors, beta=0.5)
    best = best_threshold(results)

    shipped = GraphSettings().inject_similarity_floor
    at_shipped = next(r for r in results if abs(r.threshold - shipped) < 1e-9)

    # --- evidence (captured to docs/.../evidence/) ---------------------------
    print(
        f"\nK3 injection-threshold sweep — {len(scored_labels)} labelled pairs "
        f"(bge-small-en-v1.5, F0.5)"
    )
    print(
        f"  positives (should-inject): {sum(1 for _, y in scored_labels if y)}; "
        f"negatives: {sum(1 for _, y in scored_labels if not y)}"
    )
    for r in results:
        if 0.58 <= r.threshold <= 0.74:
            mark = "  <- best" if abs(r.threshold - best.threshold) < 1e-9 else ""
            ship = "  <- shipped" if abs(r.threshold - shipped) < 1e-9 else ""
            print(
                f"  floor={r.threshold:.2f}  P={r.precision:.3f}  R={r.recall:.3f}  "
                f"F0.5={r.f_beta:.3f}{mark}{ship}"
            )
    print(
        f"  best F0.5 floor = {best.threshold:.2f} "
        f"(P={best.precision:.3f} R={best.recall:.3f} F0.5={best.f_beta:.3f})"
    )
    print(
        f"  shipped floor   = {shipped:.2f} "
        f"(P={at_shipped.precision:.3f} R={at_shipped.recall:.3f} F0.5={at_shipped.f_beta:.3f})"
    )

    # --- the shipped default is the validated operating point ----------------
    # Precision is the stuffing guard (a false inject degrades the turn); it is
    # weighted 2× (F0.5). Recall is the recoverable failure (the next relevant
    # turn injects), so its bar is softer. The shipped floor is NOT inherited from
    # K0's 0.82 link bar — at 0.82 nothing injects on this asymmetric task
    # (query↔content scores far lower); the sweep relocated it to the 0.62–0.66
    # F0.5 plateau.
    assert at_shipped.precision >= 0.85, "shipped floor stuffs (precision too low)"
    assert at_shipped.recall >= 0.45, "shipped floor starves (recall too low)"
    # The shipped default sits ON the swept F0.5 optimum (validated, not inherited).
    assert 0.60 <= shipped <= 0.70
    assert best.f_beta - at_shipped.f_beta <= 0.02
    # The K0 link bar (0.82) would have starved every turn — the regression the
    # validation caught and this guards against silently re-inheriting.
    inherited = next(r for r in results if abs(r.threshold - 0.82) < 1e-9)
    assert inherited.recall == 0.0, "the inherited K0 link bar starves every turn"
