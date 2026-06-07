"""Document upload + lifecycle service (spec 14 T13).

Composes the per-format parsers (T11 dispatcher) + the size-aware ingest
strategy (T12) + the conversation-scoped :class:`DocumentStore` (T03) +
the persona workspace (Spec 03 sandbox-path resolver) into a clean
upload-on-the-API-boundary contract.

**Workspace layout** (v0.1 — workspace-only, no DB migration):

::

    {sandbox_root}/{persona_id}/conversations/{conversation_id}/documents/
        {doc_ref}.{ext}           original bytes
        {doc_ref}.meta.json       DocumentRef sidecar (strategy, token_count,
                                  format, title, page_count, sheet_names, ...)

Why workspace + sidecar (not a DB column at v0.1): adding a
``conversations.documents JSONB`` column is a separate Alembic migration
(equivalent to Spec 13's D-13-X-now ``messages.images``). T13 does not
own that migration; the workspace + sidecar pattern lets T14/T15/T16 read
attached documents without a schema change, and a v0.2 refactor can
promote the sidecar to a DB column when the API surface grows. The
runtime impact is one ``os.listdir`` per turn (bounded by a small
document count per conversation — typically 0–10).

**Public surface:**

- :func:`upload` — validate format/size, store original bytes, parse +
  ingest, write the sidecar, return the :class:`DocumentRef`.
- :func:`list_for_conversation` — enumerate attached documents for the
  T14/T15/T16 prompt-builder extensions + the GET endpoint (T18).
- :func:`get_document_text` — read full text of a small whole-inject
  document for T14's prompt injection.
- :func:`remove_document` — per-document delete (T18 DELETE endpoint).
  Removes the workspace files (original + sidecar) AND walks the
  :class:`DocumentStore` to delete the doc's chunks (retrieval path).
- :func:`remove_all_for_conversation` — **the cascade-delete helper
  T19 reuses** when a conversation is deleted (co-landing with Spec 13's
  T12 in one DELETE-handler refactor per D-14-X-cascade-coordination).

**CSA-2 (cross-spec upload-route extension):** the route layer (T17,
gated on Spec 13's T11) dispatches by content-type to either
:func:`upload` (this module) or :func:`persona_api.services.image_service.upload`
(Spec 13's T10, not yet shipped). The signatures differ naturally —
documents are conversation-scoped, images are persona-scoped per Spec 13
§7 — but the dispatcher boundary is uniform.

**Vision-handoff-required (T13/T21 interim contract per user T13 framing):**
When :class:`~persona.documents.ingest.IngestStrategy.VISION_HANDOFF_REQUIRED`
fires (a scanned PDF, ``parse_result.needs_vision_handoff=True``), this
service deletes the workspace original (no orphans) and raises
:class:`~persona.documents.errors.VisionHandoffRequiredError`. T17 catches
this and returns a clean 422 *"vision_handoff_required"* — Spec 13 fail-
loud discipline applied at Spec 14's interim state. T21 changes this:
the ingest path dispatches to vision; the exception goes away.
"""

from __future__ import annotations

import io
import os
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from persona.documents.ingest import IngestStrategy, ingest_document
from persona.documents.parsers import SUPPORTED_EXTENSIONS, parse_document
from persona.logging import get_logger
from persona.schema.content import ImageContent
from persona.tools._sandbox import resolve_sandbox_path
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from persona.stores.document_store import DocumentStore
    from persona_runtime.prompt import DocumentContext

__all__ = [
    "DOCUMENT_DIR_NAME",
    "DocumentRef",
    "build_document_context",
    "get_document_text",
    "list_for_conversation",
    "remove_all_for_conversation",
    "remove_document",
    "upload",
]

_log = get_logger("api.documents")

#: Workspace sub-directory holding a conversation's attached documents.
DOCUMENT_DIR_NAME: str = "documents"

#: Pattern for filename-derived doc_ref slugs (matches the make_document_chunk_id
#: discipline: no ``::`` delimiter, alphanumerics + dashes + dots safe).
_DOC_REF_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class DocumentRef(BaseModel):
    """Reference to an attached document — the API-boundary type.

    Persisted alongside the original file as a ``{doc_ref}.meta.json``
    sidecar in the workspace. Returned by :func:`upload`,
    :func:`list_for_conversation`, and (via JSON) the API GET endpoint
    (T18). Carries the metadata T14/T15/T16 need to render the prompt
    sections + the synopsis.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_ref: str
    """Stable identifier within the conversation; URL-safe, no ``::``."""

    filename: str
    """The uploaded filename (control-char-stripped for safety)."""

    title: str
    """Display title — defaults to the filename; future v0.2 may allow
    a user-supplied override."""

    format: str
    """One of ``"pdf"`` / ``"docx"`` / ``"xlsx"`` / ``"csv"`` /
    ``"txt"`` / ``"md"`` / ``"code"``."""

    workspace_path: str
    """Path under ``sandbox_root`` (e.g.
    ``persona_X/conversations/conv_Y/documents/doc.pdf``)."""

    strategy: IngestStrategy
    """The ingestion path taken. ``WHOLE_INJECT`` means T14 reads the
    full text; ``RETRIEVAL`` means T15 queries the
    :class:`DocumentStore`; ``VISION_HANDOFF_REQUIRED`` cannot persist —
    :func:`upload` raises before writing the sidecar."""

    token_count: int = Field(ge=0)
    """cl100k_base estimate of the full document text."""

    page_count: int | None = None
    """For PDF/docx — total pages."""

    sheet_names: tuple[str, ...] | None = None
    """For xlsx — sheet names in workbook order."""

    size_bytes: int | None = None
    """Original file size."""

    images: tuple[ImageContent, ...] = ()
    """T21 — scanned-PDF rasterised pages as
    :class:`persona.schema.content.ImageContent` references. Empty for
    text-extracted documents; populated when ``strategy ==
    VISION_HANDOFF`` (a scanned PDF whose pages were rasterised and
    routed through Spec 13's vision-capable backends per D-13-X-pdf-contract).
    The conversation message that references this document carries these
    images in its ``content: list[MessageContent]`` so the router's
    vision pre-filter (Spec 13 T09) routes the turn to a vision-capable
    tier."""


def upload(
    *,
    sandbox_root: Path,
    persona_id: str,
    conversation_id: str,
    file_bytes: bytes,
    filename: str,
    document_store: DocumentStore,
) -> DocumentRef:
    """Validate, store, parse, ingest. Returns the persisted reference.

    Args:
        sandbox_root: Workspace root (typically ``./.persona_work``;
            D-03-23). The per-persona / per-conversation sub-tree lives
            beneath this.
        persona_id: The persona this document is uploaded to (RLS scope
            at the workspace level — a cross-tenant attempt resolves
            to a different ``sandbox_root/{persona_id}/...`` path, which
            the resolver guards via
            :func:`~persona.tools._sandbox.resolve_sandbox_path`).
        conversation_id: The conversation scope for the document
            attachment.
        file_bytes: The raw uploaded bytes.
        filename: The uploaded filename (no path components — strip at
            the route boundary).
        document_store: The conversation-scoped store for the retrieval
            path (T03).

    Returns:
        :class:`DocumentRef` with the strategy + metadata.

    Raises:
        UnsupportedFormatError: Extension not in
            :data:`persona.documents.parsers.SUPPORTED_EXTENSIONS`.
        CorruptDocumentError: Parser couldn't read the file.
        MissingDependencyError: A parser library isn't installed
            (for the ``[documents]`` extra interim state).
        VisionHandoffRequiredError: PDF needs vision processing but T21
            isn't wired yet (interim per the user's T13 framing). The
            workspace original is deleted before this raises (no
            orphans).
    """
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        from persona.documents.errors import UnsupportedFormatError  # noqa: PLC0415

        raise UnsupportedFormatError(
            "unsupported document format",
            context={
                "format": extension or "unknown",
                "filename": _safe_filename(filename),
            },
        )

    doc_ref = _make_doc_ref(filename)
    relative_path = (
        f"persona_{persona_id}/conversations/{conversation_id}"
        f"/{DOCUMENT_DIR_NAME}/{doc_ref}{extension}"
    )
    workspace_path = resolve_sandbox_path(sandbox_root, relative_path)
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path.write_bytes(file_bytes)

    try:
        parse_result = parse_document(workspace_path)
    except Exception:
        # On parser failure, clean up the workspace file so we don't leak
        # orphans. The original exception propagates.
        workspace_path.unlink(missing_ok=True)
        raise

    title = filename
    document_format = _format_for_extension(extension)

    ingest_result = ingest_document(
        parse_result=parse_result,
        conversation_id=conversation_id,
        doc_ref=doc_ref,
        title=title,
        document_format=document_format,
        document_store=document_store,
    )

    images: tuple[ImageContent, ...] = ()
    if ingest_result.strategy == IngestStrategy.VISION_HANDOFF_REQUIRED:
        # T21 — the real vision handoff. Rasterise pages via pypdfium2 +
        # Pillow, persist each page PNG under the workspace, and produce
        # ImageContent references (Spec 13's D-13-X-now option (c) shape:
        # workspace_path-only, no inlined bytes). The conversation message
        # that references this document carries these images so the
        # router's vision pre-filter (Spec 13 T09) routes the turn to a
        # vision-capable tier per D-13-X-pdf-contract.
        images = _rasterise_and_persist_pages(
            sandbox_root=sandbox_root,
            persona_id=persona_id,
            conversation_id=conversation_id,
            doc_ref=doc_ref,
            pdf_path=workspace_path,
        )
        # The ingest result is updated so the DocumentRef's strategy +
        # token_count reflect the vision path's outcome.
        ingest_result = ingest_result.model_copy(update={"strategy": IngestStrategy.VISION_HANDOFF})

    ref = DocumentRef(
        doc_ref=doc_ref,
        filename=_safe_filename(filename),
        title=title,
        format=document_format,
        workspace_path=relative_path,
        strategy=ingest_result.strategy,
        token_count=ingest_result.token_count,
        page_count=parse_result.page_count,
        sheet_names=parse_result.sheet_names,
        size_bytes=parse_result.size_bytes,
        images=images,
    )

    sidecar_path = workspace_path.with_suffix(workspace_path.suffix + ".meta.json")
    sidecar_path.write_text(ref.model_dump_json())

    _log.info(
        "document uploaded persona={} conv={} ref={} strategy={} tokens={}",
        persona_id,
        conversation_id,
        doc_ref,
        ingest_result.strategy.value,
        ingest_result.token_count,
    )

    return ref


def list_for_conversation(
    *,
    sandbox_root: Path,
    persona_id: str,
    conversation_id: str,
) -> list[DocumentRef]:
    """List attached documents in workspace order.

    Returns an empty list if the conversation directory doesn't exist
    (no documents uploaded yet).
    """
    base = _conversation_documents_dir(sandbox_root, persona_id, conversation_id)
    if not base.exists():
        return []
    refs: list[DocumentRef] = []
    for sidecar in sorted(base.glob("*.meta.json")):
        try:
            ref = DocumentRef.model_validate_json(sidecar.read_text())
        except Exception:  # noqa: BLE001 — sidecar may be corrupt; skip
            _log.warning("skipping unreadable sidecar {}", sidecar)
            continue
        refs.append(ref)
    return refs


def build_document_context(
    *,
    sandbox_root: Path,
    persona_id: str,
    conversation_id: str,
    user_message: str,
    document_store: DocumentStore | None,
    retrieval_top_k: int = 5,
) -> DocumentContext:
    """Build the per-turn :class:`persona_runtime.prompt.DocumentContext`.

    F3 follow-up — closes the third Spec 14 integration gap (after
    ``build_document_store`` wiring and the ``memory_chunks`` RLS aux
    policy). The chat path needs to thread attached documents into
    the prompt-builder; this helper is the boundary that reads from
    the workspace sidecars + queries the DocumentStore and produces
    the typed runtime shape.

    Args:
        sandbox_root: Workspace root.
        persona_id: Persona scope.
        conversation_id: Conversation scope (documents are
            conversation-scoped per Spec 14 §6).
        user_message: This turn's user message (drives RETRIEVAL ranking).
        document_store: The conversation-scoped store (returned from
            ``app.state.build_document_store``). ``None`` skips the
            RETRIEVAL path — whole-inject docs still inject their full
            text since they're sidecar-backed.
        retrieval_top_k: Per-doc cap on RETRIEVAL chunks. Defaults to 5;
            T22's D-14-X-prompt-bound-target asserts < 30 000 tokens
            even at high retrieval volume.

    Returns:
        A :class:`DocumentContext` populated with:
          - ``whole_inject_docs``: every doc with
            ``strategy == WHOLE_INJECT`` (small enough for full-text
            injection per D-14-1's 3000-token threshold);
          - ``retrieved_chunks``: per-doc top-K query results for docs
            with ``strategy == RETRIEVAL``;
          - ``attached_documents``: the synopsis row for every attached
            doc regardless of strategy (T16 structural defence).
        Empty ``DocumentContext`` when no docs are attached.
    """
    # Lazy import — runtime is api's downstream, but the runtime types are
    # safe to import at call time (api already depends on persona_runtime).
    from persona_runtime.prompt import (  # noqa: PLC0415
        DocumentContext,
        DocumentDescriptor,
        DocumentInjection,
    )

    refs = list_for_conversation(
        sandbox_root=sandbox_root,
        persona_id=persona_id,
        conversation_id=conversation_id,
    )
    if not refs:
        return DocumentContext()

    whole_inject: list[DocumentInjection] = []
    retrieved: list[Any] = []
    descriptors: list[DocumentDescriptor] = []

    for ref in refs:
        descriptors.append(
            DocumentDescriptor(
                title=ref.title,
                format=ref.format,
                page_count=ref.page_count,
                sheet_names=ref.sheet_names,
                size_bytes=ref.size_bytes,
            )
        )
        if ref.strategy == IngestStrategy.WHOLE_INJECT:
            text = get_document_text(
                sandbox_root=sandbox_root,
                persona_id=persona_id,
                conversation_id=conversation_id,
                doc_ref=ref.doc_ref,
            )
            if text:
                whole_inject.append(
                    DocumentInjection(
                        title=ref.title,
                        format=ref.format,
                        full_text=text,
                    )
                )
        elif ref.strategy == IngestStrategy.RETRIEVAL and document_store is not None:
            # Per-doc retrieval scoped by metadata.doc_ref so each doc's
            # chunks compete only within their own document (avoids one
            # noisy long doc swamping the top-K).
            try:
                chunks = document_store.query(
                    conversation_id,
                    user_message,
                    top_k=retrieval_top_k,
                    doc_ref=ref.doc_ref,
                )
            except Exception:  # noqa: BLE001 — RETRIEVAL is best-effort
                _log.warning(
                    "document retrieval failed for ref {} — skipping",
                    ref.doc_ref,
                )
                continue
            retrieved.extend(chunks)

    return DocumentContext(
        whole_inject_docs=tuple(whole_inject),
        retrieved_chunks=tuple(retrieved),
        attached_documents=tuple(descriptors),
    )


def get_document_text(
    *,
    sandbox_root: Path,
    persona_id: str,
    conversation_id: str,
    doc_ref: str,
) -> str:
    """Read the full text for a small whole-inject document.

    Used by T14's :class:`PromptBuilder` extension for the small-doc
    whole-injection section. Re-parses the workspace original — the
    parsers are idempotent and fast for the small-doc threshold.

    Args:
        sandbox_root: Workspace root.
        persona_id: Persona scope.
        conversation_id: Conversation scope.
        doc_ref: Document reference.

    Returns:
        The full extracted text. Empty string if the document isn't
        found or is unreadable (T14 handles empty cleanly).
    """
    base = _conversation_documents_dir(sandbox_root, persona_id, conversation_id)
    if not base.exists():
        return ""
    # Find the original file by doc_ref prefix (the extension is preserved).
    for candidate in base.iterdir():
        if candidate.name.startswith(f"{doc_ref}.") and not candidate.name.endswith(".meta.json"):
            try:
                return parse_document(candidate).full_text
            except Exception:  # noqa: BLE001 — fail-safe; T14 handles empty
                _log.warning("get_document_text failed for ref {}", doc_ref)
                return ""
    return ""


def remove_document(
    *,
    sandbox_root: Path,
    persona_id: str,
    conversation_id: str,
    doc_ref: str,
    document_store: DocumentStore,
) -> None:
    """Remove a single document — workspace files + DocumentStore chunks.

    Idempotent: removing a non-existent ref is a no-op.

    Args:
        sandbox_root: Workspace root.
        persona_id: Persona scope.
        conversation_id: Conversation scope.
        doc_ref: Document reference to remove.
        document_store: The conversation-scoped store — its chunks for
            this ``doc_ref`` are removed via the 4-component
            chunk-ID prefix-match (D-14-X-document-chunk-id).
    """
    base = _conversation_documents_dir(sandbox_root, persona_id, conversation_id)
    if base.exists():
        for candidate in list(base.iterdir()):
            if candidate.name.startswith(f"{doc_ref}.") or candidate.name.startswith(f"{doc_ref}."):
                # Matches both ``{doc_ref}.pdf`` (original) and
                # ``{doc_ref}.pdf.meta.json`` (sidecar) since both start
                # with ``{doc_ref}.``.
                candidate.unlink(missing_ok=True)
    document_store.delete_document(conversation_id, doc_ref)


def remove_all_for_conversation(
    *,
    sandbox_root: Path,
    persona_id: str,
    conversation_id: str,
    document_store: DocumentStore,
) -> None:
    """Cascade-delete helper — removes every document for a conversation.

    **T19 reuses this helper** when a conversation is deleted (per
    D-14-X-cascade-coordination — co-landing with Spec 13's T12 in one
    DELETE-handler refactor). Removes the entire
    ``persona_{id}/conversations/{conv}/documents/`` sub-tree from the
    workspace AND wipes the :class:`DocumentStore` chunks for the
    conversation.

    Idempotent: removing an empty / non-existent set is a no-op.
    """
    base = _conversation_documents_dir(sandbox_root, persona_id, conversation_id)
    if base.exists():
        for child in list(base.iterdir()):
            child.unlink(missing_ok=True)
        # Remove the documents sub-directory and any now-empty parents.
        base.rmdir()
        _cleanup_empty_parents(base, stop_at=sandbox_root)
    document_store.delete(conversation_id)


#: T21 — DPI for PDF page rasterisation (R-14-2 lean: 150 — sweet spot for
#: Anthropic + GPT-4o-class vision per the research; Spec 13's downscale
#: D-13-1 reduces effective post-downscale resolution to ~1568 px long edge).
#: Overridable via ``PERSONA_DOC_PDF_RASTER_DPI`` (R-14-2 + D-14-2 sub).
DEFAULT_RASTER_DPI: int = 150

#: Env var for operators tuning the rasterisation DPI (operates at the
#: rasterisation stage, NOT after Spec 13's downscale — see D-14-2 sub).
RASTER_DPI_ENV_VAR: str = "PERSONA_DOC_PDF_RASTER_DPI"


def _resolve_raster_dpi() -> int:
    """Resolve the rasterisation DPI from env override (range 100–300)."""
    raw = os.environ.get(RASTER_DPI_ENV_VAR)
    if not raw:
        return DEFAULT_RASTER_DPI
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_RASTER_DPI
    if parsed < 100 or parsed > 300:
        return DEFAULT_RASTER_DPI
    return parsed


def _rasterise_and_persist_pages(
    *,
    sandbox_root: Path,
    persona_id: str,
    conversation_id: str,
    doc_ref: str,
    pdf_path: Path,
) -> tuple[ImageContent, ...]:
    """T21 — rasterise PDF pages to PNG and persist under the workspace.

    Uses ``pypdfium2`` (BSD-3 / Apache-2.0, license-stack-clean per
    D-14-X-pdf-library-license) for rendering. Each page lands at
    ``{sandbox_root}/persona_{id}/conversations/{conv}/documents/
    {doc_ref}.page-{N:04d}.png``. Returns a tuple of
    :class:`~persona.schema.content.ImageContent` (workspace-path
    references; Spec 13 D-13-X-now option (c) shape — no inlined bytes).

    The conversation message that references this document carries these
    ``ImageContent`` instances so the Spec 13 T09 router pre-filter
    routes the turn to a vision-capable tier (D-13-X-pdf-contract).
    """
    import pypdfium2  # noqa: PLC0415 — lazy import (optional pypdfium2 dep)

    dpi = _resolve_raster_dpi()
    scale = dpi / 72.0

    base_relative = f"persona_{persona_id}/conversations/{conversation_id}/{DOCUMENT_DIR_NAME}"

    images: list[ImageContent] = []
    pdf = pypdfium2.PdfDocument(str(pdf_path))
    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            try:
                pil_image = page.render(scale=scale).to_pil()
                buf = io.BytesIO()
                pil_image.save(buf, format="PNG")
                png_bytes = buf.getvalue()
            finally:
                page.close()

            page_relative = f"{base_relative}/{doc_ref}.page-{page_index + 1:04d}.png"
            page_path = resolve_sandbox_path(sandbox_root, page_relative)
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_bytes(png_bytes)

            images.append(
                ImageContent(
                    workspace_path=page_relative,
                    media_type="image/png",
                )
            )
    finally:
        pdf.close()

    return tuple(images)


# ----- helpers (private) -----------------------------------------------------


def _conversation_documents_dir(sandbox_root: Path, persona_id: str, conversation_id: str) -> Path:
    relative = f"persona_{persona_id}/conversations/{conversation_id}/{DOCUMENT_DIR_NAME}"
    # Resolve through the sandbox helper so traversal attempts are caught even
    # for the read paths. ``resolve_sandbox_path`` raises ``SandboxViolationError``
    # on traversal; this is a programmer-error boundary for read helpers
    # (caller-supplied conversation_id must be valid).
    return resolve_sandbox_path(sandbox_root, relative)


def _make_doc_ref(filename: str) -> str:
    """Derive a stable, URL-safe doc_ref from the filename.

    Strategy: take the stem (no extension), strip unsafe chars, append a
    short uuid suffix for uniqueness within the conversation. The result
    is delimiter-free (no ``::``) per
    :func:`persona.schema.documents.make_document_chunk_id` validation.
    """
    stem = Path(filename).stem
    safe = _DOC_REF_SAFE_RE.sub("-", stem).strip("-") or "doc"
    suffix = uuid.uuid4().hex[:8]
    return f"{safe}-{suffix}"


def _safe_filename(filename: str) -> str:
    """Strip control chars + truncate filenames for audit-log + ref safety."""
    cleaned = "".join(c for c in filename if c == "\t" or ord(c) >= 32)
    return cleaned[:120]


def _format_for_extension(extension: str) -> str:
    """Map a file extension to the format string used in :class:`DocumentRef`."""
    code_extensions = {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".swift",
        ".rb",
        ".php",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
        ".sql",
        ".html",
        ".css",
        ".scss",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".xml",
    }
    if extension == ".pdf":
        return "pdf"
    if extension == ".docx":
        return "docx"
    if extension == ".xlsx":
        return "xlsx"
    if extension == ".csv":
        return "csv"
    if extension == ".md":
        return "md"
    if extension == ".txt":
        return "txt"
    if extension in code_extensions:
        return "code"
    return "unknown"


def _cleanup_empty_parents(path: Path, *, stop_at: Path) -> None:
    """Remove empty parent directories up to (but not including) ``stop_at``."""
    stop_resolved = stop_at.resolve(strict=False)
    parent = path.parent
    while parent.resolve(strict=False) != stop_resolved:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
