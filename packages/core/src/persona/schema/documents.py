"""Document-chunk primitives — the sibling shape for conversation-scoped documents.

A :class:`DocumentChunk` is the atomic unit of uploaded-document content
stored in the conversation-scoped :class:`persona.stores.document_store.DocumentStore`
(spec 14, fifth store kind). It is a **sibling** of :class:`persona.schema.chunks.PersonaChunk`,
not an extension (D-14-X-DocumentChunk-shape) — the §6 isolation discipline
(documents are working material, NOT persona identity) reads itself in the type
system: a function that takes ``PersonaChunk`` cannot be called with a
``DocumentChunk``, and vice versa, without an explicit conversion.

The ``DocumentStore`` converts ``DocumentChunk`` → ``PersonaChunk`` at the
storage boundary via :meth:`DocumentChunk.to_persona_chunk` so the existing
:class:`persona.stores.backend.Backend` transport (Chroma + Postgres,
spec 07 D-07-3) stays storage-neutral. Doc-specific fields ride in the
:class:`PersonaChunk` ``metadata: dict[str, str]`` carrier under the
standardised keys recorded in :data:`DOCUMENT_METADATA_KEYS`.

The chunk-ID format is the 4-component sibling of :func:`persona.schema.chunks.make_chunk_id`
(D-14-X-document-chunk-id): ``{conversation_id}::document::{doc_ref}::{index:04d}``
via :func:`make_document_chunk_id`. The 4-component shape lets
``DocumentStore.delete_document(conversation_id, doc_ref)`` work as a clean
prefix-match. See ``docs/specs/phase2/spec_14/decisions.md`` for the full
rationale.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from persona.schema.chunks import PersonaChunk

__all__ = [
    "DOCUMENT_METADATA_KEYS",
    "DOCUMENT_STORE_KIND",
    "DocumentChunk",
    "make_document_chunk_id",
]

#: The ``store_kind`` value the conversation-scoped document store uses.
#: Distinct from the four typed-store kinds (``identity``/``self_facts``/
#: ``worldview``/``episodic``). Reserved namespace — do not reuse for any
#: persona-scoped store.
DOCUMENT_STORE_KIND: str = "document"

#: Standardised metadata keys for document chunks at the storage boundary
#: (after :meth:`DocumentChunk.to_persona_chunk`). All values are strings —
#: :class:`persona.schema.chunks.PersonaChunk.metadata` is ``dict[str, str]``
#: per Spec 01. Numeric values like page count are stringified.
DOCUMENT_METADATA_KEYS: frozenset[str] = frozenset(
    {"doc_ref", "format", "title", "page", "section", "sheet"}
)


def _ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes; convert offset-aware datetimes to UTC.

    Mirrors :func:`persona.schema.chunks._ensure_utc` (spec 01 §11.4 — tz-aware
    UTC, always).
    """
    if value.tzinfo is None:
        msg = (
            "naive datetime not allowed; use datetime.now(timezone.utc) "
            "or attach a tzinfo (see spec_01_core.md §11.4)"
        )
        raise ValueError(msg)
    return value.astimezone(UTC)


def make_document_chunk_id(conversation_id: str, doc_ref: str, index: int) -> str:
    """Build a deterministic document-chunk identifier.

    The 4-component sibling of :func:`persona.schema.chunks.make_chunk_id`
    (D-14-X-document-chunk-id). Format::

        {conversation_id}::document::{doc_ref}::{index:04d}

    The 4-component shape encodes the doc-scope in the chunk ID so
    ``DocumentStore.delete_document(conversation_id, doc_ref)`` is a clean
    prefix-match operation; lexicographic sort matches insertion order within
    a single document.

    Args:
        conversation_id: The conversation scope identifier. Passed into
            :class:`persona.stores.backend.Backend`'s ``persona_id`` slot
            per the calling-convention discipline (D-14-X-scope-binding-discipline,
            CSA-1 in [`docs/DECISIONS.md`](../../../../docs/DECISIONS.md)).
        doc_ref: Stable reference to the parent document within the
            conversation. Lets a multi-document conversation address each
            document independently for retrieval and deletion. Conventionally
            the document's workspace-path basename or a uuid; the store does
            not interpret it beyond equality.
        index: Zero-based position of the chunk within its parent document.
            Must be non-negative and fit in 4 decimal digits when padded.

    Returns:
        A string identifier that sorts lexicographically in insertion order
        per ``(conversation_id, doc_ref)`` pair.

    Raises:
        ValueError: If ``index`` is negative, or if ``conversation_id`` or
            ``doc_ref`` contains the ``"::"`` delimiter (which would break
            prefix-matching for delete).
    """
    if index < 0:
        msg = f"chunk index must be non-negative; got {index!r}"
        raise ValueError(msg)
    if "::" in conversation_id:
        msg = f"conversation_id must not contain '::'; got {conversation_id!r}"
        raise ValueError(msg)
    if "::" in doc_ref:
        msg = f"doc_ref must not contain '::'; got {doc_ref!r}"
        raise ValueError(msg)
    return f"{conversation_id}::{DOCUMENT_STORE_KIND}::{doc_ref}::{index:04d}"


class DocumentChunk(BaseModel):
    """The atomic unit of uploaded-document content.

    Sibling of :class:`persona.schema.chunks.PersonaChunk` (D-14-X-DocumentChunk-shape),
    NOT an extension. Documents are working material, NOT persona identity
    (Dominant Concern #1); the sibling-type discipline makes the boundary
    structurally visible in the type system.

    No ``provenance`` field — documents don't carry the three-source axis
    (``system`` / ``user`` / ``persona_self``). The upload IS the provenance
    (D-14-X-no-source-policy-on-documents). No ``content_hash`` field —
    tamper-checking for transient conversation-scoped material would be
    over-engineering; the workspace original file is the canonical bytes,
    chunks are derived.

    Attributes:
        id: Stable identifier. Conventionally produced by
            :func:`make_document_chunk_id`. Backend stores it as
            :class:`persona.schema.chunks.PersonaChunk.id`.
        text: The chunk's extracted textual content.
        doc_ref: Reference to the parent document within the conversation.
            Lets multi-document conversations address each document
            independently; carried into ``PersonaChunk.metadata["doc_ref"]``
            at the storage boundary.
        format: One of ``"pdf"`` / ``"docx"`` / ``"xlsx"`` / ``"csv"`` /
            ``"txt"`` / ``"md"`` / ``"code"`` — the parser the chunk came
            from. Carried into ``PersonaChunk.metadata["format"]``.
        title: Display title for the parent document — typically the
            uploaded filename. Carried into ``PersonaChunk.metadata["title"]``.
        page: Page number (1-indexed) for PDF/docx chunks. ``None`` for
            formats without page semantics. Carried as stringified
            ``PersonaChunk.metadata["page"]``.
        section: Heading path for prose (e.g. ``"3.2 Methodology"``).
            ``None`` when not extractable. Carried as
            ``PersonaChunk.metadata["section"]``.
        sheet: Sheet name for XLSX chunks. ``None`` for other formats.
            Carried as ``PersonaChunk.metadata["sheet"]``.
        distance: Set by ``DocumentStore.query`` on retrieved chunks; never
            populated by writers.
        created_at: UTC creation timestamp. Naive datetimes are rejected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    doc_ref: str
    format: str
    title: str
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    sheet: str | None = None
    distance: float | None = None
    created_at: datetime

    @field_validator("created_at", mode="after")
    @classmethod
    def _created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        return _ensure_utc(value)

    @field_validator("doc_ref")
    @classmethod
    def _doc_ref_no_delimiter(cls, value: str) -> str:
        if "::" in value:
            msg = f"doc_ref must not contain '::'; got {value!r}"
            raise ValueError(msg)
        return value

    def to_persona_chunk(self) -> PersonaChunk:
        """Convert to the storage-boundary :class:`PersonaChunk` shape.

        Encodes the doc-specific fields into the
        :attr:`persona.schema.chunks.PersonaChunk.metadata` carrier under
        the keys recorded in :data:`DOCUMENT_METADATA_KEYS`. ``provenance``
        is ``None`` (documents don't carry the three-source axis); the
        ``content_hash`` is auto-computed by the ``PersonaChunk`` model
        validator from text + metadata.

        The reverse trip is :meth:`from_persona_chunk`.

        Returns:
            A :class:`PersonaChunk` carrying this document chunk's content
            and metadata, ready for
            :meth:`persona.stores.backend.Backend.upsert`.
        """
        # Import lazily to avoid the schema-package import cycle.
        from persona.schema.chunks import PersonaChunk

        metadata: dict[str, str] = {
            "doc_ref": self.doc_ref,
            "format": self.format,
            "title": self.title,
        }
        if self.page is not None:
            metadata["page"] = str(self.page)
        if self.section is not None:
            metadata["section"] = self.section
        if self.sheet is not None:
            metadata["sheet"] = self.sheet
        return PersonaChunk(
            id=self.id,
            text=self.text,
            metadata=metadata,
            created_at=self.created_at,
            provenance=None,
        )

    @classmethod
    def from_persona_chunk(cls, chunk: PersonaChunk) -> DocumentChunk:
        """Reconstruct a :class:`DocumentChunk` from a stored :class:`PersonaChunk`.

        The inverse of :meth:`to_persona_chunk`. Reads doc-specific fields
        from :attr:`persona.schema.chunks.PersonaChunk.metadata` under the
        keys recorded in :data:`DOCUMENT_METADATA_KEYS`. Carries the
        ``distance`` field over (populated by ``Backend.query``).

        Args:
            chunk: A :class:`PersonaChunk` previously written via
                :meth:`to_persona_chunk` (or otherwise tagged with the
                document-store metadata convention).

        Returns:
            A reconstructed :class:`DocumentChunk`.

        Raises:
            ValueError: If a required metadata key (``doc_ref`` / ``format``
                / ``title``) is missing from ``chunk.metadata``. Indicates a
                non-document-store chunk was passed in error.
        """
        for required in ("doc_ref", "format", "title"):
            if required not in chunk.metadata:
                msg = (
                    f"chunk.metadata missing required document key {required!r}; "
                    "not a document-store chunk"
                )
                raise ValueError(msg)
        page_str = chunk.metadata.get("page")
        page = int(page_str) if page_str is not None else None
        return cls(
            id=chunk.id,
            text=chunk.text,
            doc_ref=chunk.metadata["doc_ref"],
            format=chunk.metadata["format"],
            title=chunk.metadata["title"],
            page=page,
            section=chunk.metadata.get("section"),
            sheet=chunk.metadata.get("sheet"),
            distance=chunk.distance,
            created_at=chunk.created_at,
        )
