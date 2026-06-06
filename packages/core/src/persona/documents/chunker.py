"""Document-aware chunking — natural boundaries with token-aware fallback.

T05 (spec 14). Produces :class:`persona.schema.documents.DocumentChunk`
instances from parser-emitted structural :class:`DocumentSection` units.

Strategy (D-14-4):

1. **Natural-boundary first.** The parsers (T06–T10) emit ``DocumentSection``
   per paragraph / heading-block / sheet / page so the chunker can keep
   semantically-coherent units together where they fit. Retrieved chunks
   then read as whole paragraphs or whole sheets, not as arbitrary slices.
2. **Token-aware fallback.** When a single ``DocumentSection`` exceeds the
   chunk-token cap, it is recursively split on paragraph (``\\n\\n``) →
   line (``\\n``) → space → character until each split is under the cap.
   This is the inline "Spec 01 fallback" path D-14-4 names — the four typed
   stores' chunking (one chunk per YAML field, no splitting) is untouched.
3. **Overlap.** Adjacent chunks within the same section carry ``overlap_tokens``
   of text overlap so retrieval over a chunk boundary doesn't lose semantic
   continuity. Default 64 tokens (R-14-3 + predecessor convention at
   ``uia-rag-chatbot/src/uia_rag/chunking/splitter.py:27``).

Defaults (D-14-4 + R-14-3):

- ``chunk_size_tokens = 512`` — practitioner consensus for
  ``bge-small-en-v1.5``-class embedders + the predecessor's ``SentenceChunker``
  default.
- ``overlap_tokens = 64`` — same source.

Token counting goes through :func:`persona.skills.count_tokens` (the shared
``cl100k_base`` encoder, D-05-8). Estimate-for-budgeting; never conflated
with provider ``usage`` for billing.

This module deliberately does NOT modify
:mod:`persona.schema.chunks` /  :mod:`persona.registry` / the four typed
stores. The Phase 1 chunking contract (one ``PersonaChunk`` per
identity/self-facts/worldview/episodic field, no splitting) stays byte-for-
byte unchanged — see :func:`chunk_document`'s module-level docstring and
the regression assertion in
``packages/core/tests/unit/documents/test_chunker.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from persona.schema.documents import DocumentChunk, make_document_chunk_id
from persona.skills import count_tokens

__all__ = [
    "DEFAULT_CHUNK_SIZE_TOKENS",
    "DEFAULT_OVERLAP_TOKENS",
    "DocumentSection",
    "chunk_document",
]

#: Default per-chunk token cap (R-14-3 + predecessor convention).
DEFAULT_CHUNK_SIZE_TOKENS: int = 512

#: Default overlap between adjacent chunks within a section.
DEFAULT_OVERLAP_TOKENS: int = 64


class DocumentSection(BaseModel):
    """A parser-emitted natural-boundary unit of document text.

    Parsers (T06–T10) emit a list of these representing the document's
    structural shape: one section per paragraph for prose, per sheet for
    spreadsheets, per page for PDFs (or a finer granularity where the
    parser can infer it). The chunker uses the metadata to stamp each
    resulting :class:`DocumentChunk` so retrieved chunks can cite their
    source location.

    Attributes:
        text: The section's textual content.
        page: 1-indexed page number, when applicable (PDF, docx).
        section: Heading path or section identifier ("3.2 Methodology"),
            when applicable (prose).
        sheet: Sheet name, when applicable (XLSX).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    sheet: str | None = None


def chunk_document(
    sections: list[DocumentSection],
    *,
    conversation_id: str,
    doc_ref: str,
    document_format: str,
    title: str,
    chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[DocumentChunk]:
    """Split sections into :class:`DocumentChunk` instances.

    Empty input or all-empty sections produce an empty list (fail-safe; the
    predecessor's :func:`uia_rag.chunking.splitter.SentenceChunker.chunk_document`
    pattern).

    Args:
        sections: Parser-emitted natural-boundary units, in document order.
        conversation_id: Conversation scope. Stamped onto each chunk's ID
            via :func:`persona.schema.documents.make_document_chunk_id`.
        doc_ref: Document reference within the conversation. Stamped onto
            each chunk's ID + metadata.
        document_format: One of ``"pdf"`` / ``"docx"`` / ``"xlsx"`` /
            ``"csv"`` / ``"txt"`` / ``"md"`` / ``"code"``.
        title: Display title for the parent document.
        chunk_size_tokens: Per-chunk token cap (default 512, R-14-3).
        overlap_tokens: Overlap between adjacent chunks within one section
            (default 64). Must be strictly less than ``chunk_size_tokens``.

    Returns:
        Ordered list of :class:`DocumentChunk` instances, indexed
        ``0..N-1``. Empty list for empty/whitespace-only input.

    Raises:
        ValueError: If ``chunk_size_tokens`` is non-positive or
            ``overlap_tokens`` is negative / not strictly less than
            ``chunk_size_tokens``.
    """
    if chunk_size_tokens <= 0:
        msg = f"chunk_size_tokens must be positive; got {chunk_size_tokens}"
        raise ValueError(msg)
    if overlap_tokens < 0:
        msg = f"overlap_tokens must be non-negative; got {overlap_tokens}"
        raise ValueError(msg)
    if overlap_tokens >= chunk_size_tokens:
        msg = (
            f"overlap_tokens ({overlap_tokens}) must be strictly less than "
            f"chunk_size_tokens ({chunk_size_tokens})"
        )
        raise ValueError(msg)

    chunks: list[DocumentChunk] = []
    created_at = datetime.now(UTC)
    global_index = 0

    for section in sections:
        cleaned = section.text.strip()
        if not cleaned:
            continue
        pieces = _split_to_token_cap(
            cleaned,
            chunk_size_tokens=chunk_size_tokens,
            overlap_tokens=overlap_tokens,
        )
        for piece in pieces:
            chunks.append(
                DocumentChunk(
                    id=make_document_chunk_id(conversation_id, doc_ref, global_index),
                    text=piece,
                    doc_ref=doc_ref,
                    format=document_format,
                    title=title,
                    page=section.page,
                    section=section.section,
                    sheet=section.sheet,
                    created_at=created_at,
                )
            )
            global_index += 1

    return chunks


def _split_to_token_cap(
    text: str,
    *,
    chunk_size_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Split ``text`` recursively on natural boundaries to fit under the cap.

    Boundary order: paragraph (``\\n\\n``) → line (``\\n``) → space → char.
    Adjacent splits within this function carry ``overlap_tokens`` of overlap.

    Pure function; no side effects.
    """
    if count_tokens(text) <= chunk_size_tokens:
        return [text]

    # Recursive split: try the coarsest separator first.
    for separator in ("\n\n", "\n", " "):
        if separator in text:
            parts = [p for p in text.split(separator) if p.strip()]
            if len(parts) > 1:
                return _pack_with_overlap(
                    parts,
                    separator=separator,
                    chunk_size_tokens=chunk_size_tokens,
                    overlap_tokens=overlap_tokens,
                )

    # No natural separator — fall back to character-level split with overlap.
    return _split_chars_with_overlap(
        text,
        chunk_size_tokens=chunk_size_tokens,
        overlap_tokens=overlap_tokens,
    )


def _pack_with_overlap(
    parts: list[str],
    *,
    separator: str,
    chunk_size_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Greedily pack parts into chunks under the cap, with token overlap.

    Each part is checked against the cap first; an over-cap part is
    recursively split via :func:`_split_to_token_cap`, and its sub-pieces
    are emitted directly (never re-packed with same-level neighbours —
    they're already cap-sized).
    """
    result: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def _flush() -> None:
        nonlocal current, current_tokens
        if current:
            result.append(separator.join(current))
            current = []
            current_tokens = 0

    for part in parts:
        part_tokens = count_tokens(part)
        if part_tokens > chunk_size_tokens:
            # Over-cap part — recursively split and emit each sub-piece
            # directly. Flush the in-progress chunk first so order is preserved.
            _flush()
            sub_pieces = _split_to_token_cap(
                part,
                chunk_size_tokens=chunk_size_tokens,
                overlap_tokens=overlap_tokens,
            )
            for piece in sub_pieces:
                result.append(piece)
            continue

        sep_tokens = count_tokens(separator) if current else 0
        if current and current_tokens + sep_tokens + part_tokens > chunk_size_tokens:
            packed = separator.join(current)
            result.append(packed)
            # Carry the overlap forward.
            overlap_text = _trailing_overlap(packed, overlap_tokens=overlap_tokens)
            current = [overlap_text] if overlap_text else []
            current_tokens = count_tokens(overlap_text) if overlap_text else 0
            sep_tokens = count_tokens(separator) if current else 0
        current.append(part)
        current_tokens += sep_tokens + part_tokens

    _flush()
    return result


def _split_chars_with_overlap(
    text: str,
    *,
    chunk_size_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Character-level fallback split with overlap.

    Uses binary search to find a character window whose token-count is at
    most ``chunk_size_tokens``. Slow path — only reached when no natural
    separator exists in the text.
    """
    result: list[str] = []
    cursor = 0
    n = len(text)
    while cursor < n:
        window_end = _binary_find_char_end(text, cursor, chunk_size_tokens=chunk_size_tokens)
        result.append(text[cursor:window_end])
        if window_end >= n:
            break
        # Pull the cursor back by ``overlap_tokens`` worth of characters
        # (approximate: char-token ratio is rough but acceptable here).
        overlap_chars = _approx_chars_for_tokens(text[cursor:window_end], overlap_tokens)
        cursor = max(cursor + 1, window_end - overlap_chars)
    return result


def _binary_find_char_end(text: str, start: int, *, chunk_size_tokens: int) -> int:
    """Find largest ``end`` such that ``count_tokens(text[start:end])`` ≤ cap."""
    n = len(text)
    lo, hi = start + 1, n
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_tokens(text[start:mid]) <= chunk_size_tokens:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _approx_chars_for_tokens(reference_text: str, overlap_tokens: int) -> int:
    """Approximate the character count for ``overlap_tokens`` worth of text."""
    if not reference_text or overlap_tokens <= 0:
        return 0
    ref_tokens = count_tokens(reference_text)
    if ref_tokens == 0:
        return 0
    char_per_token = max(1, len(reference_text) // ref_tokens)
    return char_per_token * overlap_tokens


def _trailing_overlap(packed_text: str, *, overlap_tokens: int) -> str:
    """Return the trailing ``overlap_tokens``-worth slice of ``packed_text``."""
    if overlap_tokens <= 0:
        return ""
    if count_tokens(packed_text) <= overlap_tokens:
        return packed_text
    # Binary search for the cut-point.
    lo, hi = 0, len(packed_text)
    while lo < hi:
        mid = (lo + hi) // 2
        if count_tokens(packed_text[mid:]) <= overlap_tokens:
            hi = mid
        else:
            lo = mid + 1
    return packed_text[lo:]
