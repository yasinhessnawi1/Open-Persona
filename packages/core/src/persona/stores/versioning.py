"""Pure-function helpers for the versioned, append-only update model.

See ``docs/specs/spec_01/spec_01_core.md`` §5.2.1 and architecture v0.3 §4.3.
The four typed stores share these helpers; the helpers know nothing about
ChromaDB or any other transport.

Vocabulary recap:
- ``logical_id``: the stable identifier that groups all versions of "the
  same fact". Equal to the chunk's ``id`` on first write (D-01-8).
- ``version``: monotonic per ``logical_id`` starting at 1.
- ``superseded_by``: pointer from version N to version N+1's ``id``. The
  current (head) version of a chain has ``superseded_by=None``.
- A chain is the list of all versions for one ``logical_id``, oldest first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.errors import BrokenVersionChainError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from persona.schema.chunks import PersonaChunk

__all__ = [
    "compute_next_version",
    "current_version",
    "link_supersedes",
    "validate_chain",
]


def _provenance_or_raise(chunk: PersonaChunk) -> str:
    """Return ``chunk.provenance.logical_id`` or raise if provenance is absent.

    Identity-store chunks have no provenance and never pass through these
    helpers; if one does, the caller has wired the policy table wrong.
    """
    if chunk.provenance is None:
        msg = "expected chunk with provenance; got identity-store chunk"
        raise BrokenVersionChainError(msg, context={"chunk_id": chunk.id})
    return chunk.provenance.logical_id


def compute_next_version(existing: Iterable[PersonaChunk], logical_id: str) -> int:
    """Return the version number for an append to ``logical_id``'s chain.

    Args:
        existing: All chunks the store currently holds for this persona.
            We filter by ``logical_id`` inside.
        logical_id: The logical chain being appended to. If no existing
            chunk has this ``logical_id``, the new version is 1.

    Returns:
        ``max(existing.version) + 1`` for the matching chain, or 1 if the
        chain is empty.
    """
    versions = [
        c.provenance.version
        for c in existing
        if c.provenance is not None and c.provenance.logical_id == logical_id
    ]
    return max(versions) + 1 if versions else 1


def current_version(existing: Iterable[PersonaChunk], logical_id: str) -> PersonaChunk | None:
    """Return the head (non-superseded) chunk in a logical chain, or None.

    A chain may have at most one head at any time. If the input contains
    multiple non-superseded versions for the same ``logical_id``, that is a
    broken chain — raise rather than silently pick one.
    """
    heads = [
        c
        for c in existing
        if c.provenance is not None
        and c.provenance.logical_id == logical_id
        and c.provenance.superseded_by is None
    ]
    if not heads:
        return None
    if len(heads) > 1:
        msg = "multiple head versions in chain"
        raise BrokenVersionChainError(
            msg,
            context={"logical_id": logical_id, "heads": ",".join(c.id for c in heads)},
        )
    return heads[0]


def link_supersedes(prev: PersonaChunk, new_id: str) -> PersonaChunk:
    """Return a copy of ``prev`` with ``superseded_by`` set to ``new_id``.

    Frozen Pydantic models cannot be mutated in place, so we use
    ``model_copy``. The provenance sub-model is replaced wholesale because
    it is also frozen.
    """
    _ = _provenance_or_raise(prev)
    assert prev.provenance is not None  # narrowing for type checker
    new_provenance = prev.provenance.model_copy(update={"superseded_by": new_id})
    return prev.model_copy(update={"provenance": new_provenance})


def validate_chain(chain: list[PersonaChunk]) -> None:
    """Raise :class:`BrokenVersionChainError` if ``chain`` is malformed.

    Validates four invariants:
    1. Every chunk has provenance.
    2. All chunks share one ``logical_id``.
    3. Versions form a contiguous sequence starting at 1.
    4. Each non-tail chunk's ``superseded_by`` points to the next chunk's
       ``id``; the tail has ``superseded_by=None``.

    An empty chain is treated as valid (a logical id that has not been
    written to yet has nothing to validate).
    """
    if not chain:
        return

    # 1. Provenance on every chunk.
    for c in chain:
        if c.provenance is None:
            msg = "chain member missing provenance"
            raise BrokenVersionChainError(msg, context={"chunk_id": c.id})

    # 2. Single logical_id.
    logical_ids = {c.provenance.logical_id for c in chain if c.provenance is not None}
    if len(logical_ids) != 1:
        msg = "chain spans multiple logical_ids"
        raise BrokenVersionChainError(msg, context={"logical_ids": ",".join(sorted(logical_ids))})
    logical_id = next(iter(logical_ids))

    # 3. Contiguous versions starting at 1.
    sorted_chain = sorted(
        chain,
        key=lambda c: c.provenance.version if c.provenance else 0,
    )
    versions = [c.provenance.version for c in sorted_chain if c.provenance is not None]
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        msg = "chain versions are not contiguous starting at 1"
        raise BrokenVersionChainError(
            msg,
            context={
                "logical_id": logical_id,
                "got": ",".join(str(v) for v in versions),
                "expected": ",".join(str(v) for v in expected),
            },
        )

    # 4. Supersedes links.
    for i, c in enumerate(sorted_chain):
        if c.provenance is None:
            continue  # impossible after the first check, but keeps the type checker happy
        is_tail = i == len(sorted_chain) - 1
        if is_tail:
            if c.provenance.superseded_by is not None:
                msg = "tail version must have superseded_by=None"
                raise BrokenVersionChainError(
                    msg,
                    context={
                        "logical_id": logical_id,
                        "tail_id": c.id,
                        "got": c.provenance.superseded_by,
                    },
                )
        else:
            next_chunk = sorted_chain[i + 1]
            if c.provenance.superseded_by != next_chunk.id:
                msg = "supersedes pointer does not match next chunk id"
                raise BrokenVersionChainError(
                    msg,
                    context={
                        "logical_id": logical_id,
                        "from_id": c.id,
                        "got": str(c.provenance.superseded_by),
                        "expected": next_chunk.id,
                    },
                )
