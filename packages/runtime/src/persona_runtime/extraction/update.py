"""Update/contradiction target resolution (Spec K2, T4; criterion 9).

When the user corrects an earlier statement, the extractor sets ``update_intent``
and a free-text ``update_target_hint`` (it cannot know node ids). This resolver
finds the existing graph node the correction is about — via K0's dense read leg —
so the assembler (T7/T8) hands ``GraphStore.merge`` a ``target_node_id`` and merge
EVOLVES that node with provenance (no silent overwrite, D-K0-4; this exercises K0
criterion 5 from the write side).

Safety posture: a wrong target would evolve the WRONG node, which is worse than
missing the update. So a non-confident top match resolves to ``None`` (the
correction then merges as a fresh node). The confidence bar is a cosine-distance
threshold, config-driven and flagged for real-data re-tune (the D-K0-1 posture).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.graph.protocol import UpdateIntent

if TYPE_CHECKING:
    from persona.extraction import ExtractionCandidate
    from persona.graph.protocol import GraphStore

__all__ = ["UpdateResolver"]

# Max cosine distance for a hint→node match to be trusted (lower = closer). A
# conservative default — a wrong update is worse than a missed one. Flagged for
# real-data re-tune (D-K0-1 posture); env-bound at composition.
_DEFAULT_MAX_DISTANCE = 0.4


class UpdateResolver:
    """Resolves a candidate's ``update_target_hint`` to an existing node id.

    Args:
        store: K0's graph store (the dense read leg is used).
        max_distance: Confidence bar — a top match farther than this is rejected
            (resolves to ``None``) so a correction never evolves the wrong node.
    """

    def __init__(self, *, store: GraphStore, max_distance: float = _DEFAULT_MAX_DISTANCE) -> None:
        self._store = store
        self._max_distance = max_distance

    def resolve_target(self, owner_id: str, candidate: ExtractionCandidate) -> str | None:
        """Return the existing node id this candidate updates, or ``None``.

        ``None`` when the candidate is not an update, carries no hint, the graph
        has no match, or the top match is below the confidence bar.
        """
        if candidate.update_intent is UpdateIntent.NONE or not candidate.update_target_hint:
            return None
        hits = self._store.search_dense(owner_id, candidate.update_target_hint, 1)
        if not hits:
            return None
        top = hits[0]
        if top.distance is None or top.distance > self._max_distance:
            return None
        return top.id
