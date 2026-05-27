"""The ``MemoryStore`` protocol — the port every backend implements.

Two implementations ship in v0.1: the abstract :class:`TypedStore` (in
``persona.stores.base``) plus four kind-specific subclasses; and the
concrete :class:`ChromaMemoryStore` (in ``persona.stores.chroma``) that the
subclasses delegate to. Spec 07 adds ``PostgresPGVectorStore`` behind this
same protocol.

The surface is wider than Persona-RAG's because the three-source policy
model (``WriteSource``) and the versioned, append-only update model
(``history``, ``rollback``) live here. Identity-store implementations may
reject ``history``/``rollback`` calls; the policy is store-specific.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from persona.schema.chunks import PersonaChunk, WriteSource

__all__ = ["MemoryStore"]


@runtime_checkable
class MemoryStore(Protocol):
    """The contract every memory store implements.

    All methods take a ``persona_id`` so a single store can be multi-tenant
    in v0.1 single-process and trivially extend to multi-tenant in spec 07.

    Every successful mutation emits exactly one ``AuditEvent`` via an
    injected ``AuditLogger`` (spec 01 §7.4). Rejected mutations do NOT
    audit — they log through ``persona.logging`` instead.
    """

    def write(
        self,
        persona_id: str,
        chunks: list[PersonaChunk],
        *,
        source: WriteSource,
        written_by: str | None = None,
        reason: str | None = None,
        force: bool = False,
    ) -> None:
        """Append the chunks to the store, enforcing per-source policy.

        Writes are append-only with respect to logical ids: if a chunk's
        ``provenance.logical_id`` already exists in the store, this becomes
        version N+1 and the previous head's ``superseded_by`` is updated.
        First-time logical ids become version 1.

        Raises:
            RuntimeWriteForbiddenError: System/user write violates policy.
            PersonaSelfWriteForbiddenError: Persona_self write violates
                policy (subclass of RuntimeWriteForbiddenError; callers can
                catch either).
            AuditWriteError: The audit event could not be persisted.
        """
        ...

    def query(
        self,
        persona_id: str,
        query: str,
        top_k: int,
        **filters: Any,  # noqa: ANN401 — backend-specific filter shape
    ) -> list[PersonaChunk]:
        """Return the top-k current (non-superseded) chunks for ``query``.

        ``filters`` is backend-specific; ChromaMemoryStore accepts the
        kwargs supported by Chroma's ``where=`` argument.
        """
        ...

    def get_all(
        self,
        persona_id: str,
        *,
        include_superseded: bool = False,
    ) -> list[PersonaChunk]:
        """Return every chunk for ``persona_id``.

        With ``include_superseded=False`` (the default), only the current
        head of each logical chain is returned. With ``True``, the full
        history is returned (oldest first, grouped by ``logical_id``).
        """
        ...

    def delete(self, persona_id: str) -> None:
        """Remove every chunk for ``persona_id``. Idempotent."""
        ...

    def remove_documents(self, persona_id: str, doc_ids: list[str]) -> None:
        """Remove the listed chunk ids (not logical ids). Idempotent."""
        ...

    def history(self, persona_id: str, logical_id: str) -> list[PersonaChunk]:
        """Return every version for ``logical_id``, oldest first.

        Empty list if the logical chain doesn't exist (not an error).
        """
        ...

    def rollback(
        self,
        persona_id: str,
        logical_id: str,
        to_version: int,
        *,
        source: WriteSource,
        written_by: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Append a new head identical to the targeted version.

        Append-only — never deletes. Marks intermediate versions as
        superseded by the new head. Rolling forward again is symmetric.

        Raises:
            BrokenVersionChainError: ``logical_id`` or ``to_version`` does
                not exist.
        """
        ...
