"""The prompt builder (T05; D-05-6, D-05-7, D-05-8).

Assembles the model-ready prompt as a ``list[ConversationMessage]``: one system
message (identity → constraints → self-facts → worldview → episodic → skill
index → active skill content → footer, per spec §5.1), then the compacted +
recent history, then the current user message.

Two budgets, kept distinct:

- The **skill content** budget (2000 tokens) is owned and enforced by
  :class:`persona.skills.SkillInjector` (D-04-7). The builder receives
  already-budgeted ``matched_skill_content`` and splices it verbatim — it does
  NOT define ``SKILL_TOKEN_BUDGET`` and does NOT re-enforce (D-05-7).
- The **whole-prompt** budget is the backend's context window (``max_tokens``).
  When the assembled prompt would exceed it, retrieved context is dropped in
  order — episodic → worldview → self-facts (lowest-relevance first) — and then
  history is truncated more aggressively. Identity, constraints, and the skill
  index are the persona floor and are never dropped (spec §5.3).

Token estimation uses ``persona.skills.count_tokens`` (the shared
``cl100k_base`` encoder, D-05-8) — an *estimate* for budgeting. The exact token
counts in :class:`persona_runtime.logging.TurnLog` come from the backend's
``usage`` field, post-call. Don't conflate the two.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.schema.chunks import PersonaChunk  # noqa: TC002 — Pydantic needs runtime ref
from persona.schema.conversation import ConversationMessage
from persona.schema.documents import DocumentChunk  # noqa: TC002 — Pydantic needs runtime ref
from persona.skills import count_tokens
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from persona.schema.persona import Persona

__all__ = [
    "DocumentContext",
    "DocumentDescriptor",
    "DocumentInjection",
    "PromptBuilder",
    "RetrievedContext",
]

_FOOTER = "Stay in character. Cite sources when using tool results."


class DocumentInjection(BaseModel):
    """A single attached document's whole-text payload for prompt injection.

    Sibling type to the persona-scoped retrieval bundle (D-14-X-DocumentChunk-shape
    extended to the prompt-builder layer). Spec 14 T14 — small docs (under
    D-14-1's threshold) get their whole text injected; T15 will extend
    :class:`DocumentContext` with retrieved chunks; T16 reads the
    page_count/sheet_names/size_bytes fields for the synopsis.

    All fields are immutable so a ``DocumentInjection`` can be carried
    across turns without leakage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    """Display title — typically the uploaded filename."""

    format: str
    """One of ``"pdf"`` / ``"docx"`` / ``"xlsx"`` / ``"csv"`` /
    ``"txt"`` / ``"md"`` / ``"code"``."""

    full_text: str
    """The document's extracted text. For small whole-inject docs only;
    large retrieval docs leave this empty (T15 will carry chunks in a
    sibling field)."""


class DocumentDescriptor(BaseModel):
    """Synopsis-source descriptor per attached document (Spec 14 T16).

    The "what's in scope" synopsis renders one line per attached document
    from this data. Deterministic + free per D-14-X-synopsis-source — no
    LLM call at ingest. The fields are populated from
    :class:`persona_api.services.document_service.DocumentRef`'s
    metadata at the runtime composition boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    format: str
    page_count: int | None = None
    sheet_names: tuple[str, ...] | None = None
    size_bytes: int | None = None


class DocumentContext(BaseModel):
    """Per-turn document attachments for :class:`PromptBuilder`.

    A **sibling** of :class:`RetrievedContext`, not an extension — same
    discipline as :class:`persona.schema.documents.DocumentChunk` being a
    sibling of :class:`persona.schema.chunks.PersonaChunk` (Dominant
    Concern #1: documents are working material, NOT persona identity).
    Keeping the two contexts as distinct types makes the §6 isolation
    discipline structurally visible at the prompt-builder boundary.

    T14 ships ``whole_inject_docs``. T15 extends with ``retrieved_chunks``
    (large-doc retrieval). T16 extends with ``attached_documents``
    (synopsis source — present every turn under retrieval per the
    Dominant Concern #2 structural defence).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    whole_inject_docs: tuple[DocumentInjection, ...] = ()
    """Small docs (under D-14-1's token threshold) whose full text gets
    injected verbatim. T14's substance — rendered between active skill
    content and the footer per the kickoff lean."""

    retrieved_chunks: tuple[DocumentChunk, ...] = ()
    """Per-turn retrieval results for large docs (over D-14-1's threshold).
    Ranked **above episodic** when non-empty per D-14-5 (conservative —
    episodic keeps floor-rank when no doc chunks were retrieved this
    turn). Drop after episodic but before worldview under budget pressure
    per D-14-5's reduction-ladder note."""

    attached_documents: tuple[DocumentDescriptor, ...] = ()
    """**Every** document attached to the conversation, regardless of
    whether its chunks were retrieved this turn. T16 — the structural
    defence for Dominant Concern #2. Renders as a one-line-per-doc
    synopsis section in every turn under retrieval; the model knows
    documents exist even when no chunks were injected this turn, so it
    can reason about coverage ("based on the sections I can see…")
    instead of mistaking retrieved fragments for the whole."""


class RetrievedContext(BaseModel):
    """The per-turn retrieval the loop fills from the stores (D-05-6).

    A frozen bundle so the :class:`PromptBuilder` can be tested without a live
    store. ``identity`` comes from ``identity.get_all(persona_id)``; the other
    three from ``query(persona_id, message, top_k=3)``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: list[PersonaChunk] = Field(default_factory=list)
    self_facts: list[PersonaChunk] = Field(default_factory=list)
    worldview: list[PersonaChunk] = Field(default_factory=list)
    episodic: list[PersonaChunk] = Field(default_factory=list)


class PromptBuilder:
    """Assembles the system prompt + history + user message (spec §5)."""

    def build(
        self,
        persona: Persona,
        context: RetrievedContext,
        history: list[ConversationMessage],
        skill_index: str,
        user_message: str,
        *,
        max_tokens: int,
        matched_skill_content: str | None = None,
        document_context: DocumentContext | None = None,
    ) -> list[ConversationMessage]:
        """Build the full prompt as a message list.

        Args:
            persona: The active persona (identity + constraints come from here,
                always — never truncated).
            context: The retrieved self-facts / worldview / episodic chunks.
            history: Compacted summary + recent verbatim turns (from the history
                manager). Does NOT include the current user message.
            skill_index: The rendered "available skills" block (may be empty).
            user_message: The current turn's user message — appended last.
            max_tokens: The backend's context-window budget. The assembled
                prompt is reduced (retrieved context dropped, then history
                truncated) to fit.
            matched_skill_content: Already-budgeted active-skill content from
                the injector (D-05-7). ``None`` when no skill is active.

        Returns:
            ``[system_message, *history, user_message]`` — sized to ``max_tokens``.
        """
        reduced_context = context
        trimmed_history = list(history)

        # Build once, then reduce only if over budget (the common case fits).
        messages = self._assemble(
            persona,
            reduced_context,
            trimmed_history,
            skill_index,
            user_message,
            matched_skill_content,
            document_context,
        )
        if self._token_total(messages) <= max_tokens:
            return messages

        # Over budget: drop retrieved context per the §5.3 + D-14-5 ladder
        # (episodic → docs → worldview → self-facts).
        reduced_docs = document_context
        for dropped_ctx, dropped_docs in self._reductions(reduced_context, document_context):
            reduced_context = dropped_ctx
            reduced_docs = dropped_docs
            messages = self._assemble(
                persona,
                reduced_context,
                trimmed_history,
                skill_index,
                user_message,
                matched_skill_content,
                reduced_docs,
            )
            if self._token_total(messages) <= max_tokens:
                return messages

        # Still over after zeroing retrieved context: truncate history harder
        # (drop oldest verbatim turns; keep the most recent).
        while trimmed_history and self._token_total(messages) > max_tokens:
            trimmed_history = trimmed_history[1:]
            messages = self._assemble(
                persona,
                reduced_context,
                trimmed_history,
                skill_index,
                user_message,
                matched_skill_content,
                reduced_docs,
            )
        return messages

    def _assemble(
        self,
        persona: Persona,
        context: RetrievedContext,
        history: list[ConversationMessage],
        skill_index: str,
        user_message: str,
        matched_skill_content: str | None,
        document_context: DocumentContext | None = None,
    ) -> list[ConversationMessage]:
        """Compose the message list in the spec §5.1 order."""
        system_text = self._render_system(
            persona, context, skill_index, matched_skill_content, document_context
        )
        now = datetime.now(UTC)
        system = ConversationMessage(role="system", content=system_text, created_at=now)
        user = ConversationMessage(role="user", content=user_message, created_at=now)
        return [system, *history, user]

    def _render_system(
        self,
        persona: Persona,
        context: RetrievedContext,
        skill_index: str,
        matched_skill_content: str | None,
        document_context: DocumentContext | None = None,
    ) -> str:
        """Render the system block in the spec §5.1 ordering."""
        parts: list[str] = []

        # 1. Identity opener.
        ident = persona.identity
        parts.append(f"You are {ident.name}, {ident.role}.\n{ident.background}".rstrip())

        # 2. Constraints ("You must NOT:" numbered list).
        if ident.constraints:
            lines = ["You must NOT:"]
            lines += [f"{i}. {c}" for i, c in enumerate(ident.constraints, start=1)]
            parts.append("\n".join(lines))

        # 3. Self-facts.
        if context.self_facts:
            lines = ["Relevant facts about yourself:"]
            lines += [f"- {c.text}" for c in context.self_facts]
            parts.append("\n".join(lines))

        # 4. Worldview (epistemic tags in parentheses).
        if context.worldview:
            lines = ["Your views:"]
            for c in context.worldview:
                tag = c.metadata.get("epistemic")
                suffix = f" ({tag})" if tag else ""
                lines.append(f"- {c.text}{suffix}")
            parts.append("\n".join(lines))

        # 4a. Attached documents synopsis — T16 (D-14-X-synopsis-source).
        # The structural defence against confident-but-incomplete answers
        # (Dominant Concern #2): the model is told what document(s) exist
        # EVERY turn, regardless of whether THIS turn's retrieval pulled any
        # chunks from them. Auto-generated, deterministic, no LLM at ingest.
        if document_context and document_context.attached_documents:
            parts.append(self._render_attached_documents_synopsis(document_context))

        # 4b. Retrieved document chunks — T15 retrieval-injection.
        # D-14-5 conservative rank: docs rank ABOVE episodic ONLY for
        # documents whose chunks were retrieved this turn. When empty,
        # episodic keeps its floor-rank (no section emitted).
        if document_context and document_context.retrieved_chunks:
            parts.append(self._render_retrieved_document_chunks(document_context))

        # 5. Episodic.
        if context.episodic:
            lines = ["From earlier conversations:"]
            lines += [f"- {c.text}" for c in context.episodic]
            parts.append("\n".join(lines))

        # 6. Skill index (already rendered; empty string when no skills).
        if skill_index:
            parts.append(skill_index)

        # 7. Active skill content (already budget-sized by the injector).
        if matched_skill_content:
            parts.append(matched_skill_content)

        # 8. Attached small documents — T14 whole-injection (D-14-1 small-doc
        # path). Placement per the kickoff lean: between active skill content
        # and the footer so identity + constraints + skill index stay above
        # documents per the §5.3 floor. T15 will add a retrieval-injection
        # section ABOVE episodic per D-14-5; T16 will add the "what's in
        # scope" synopsis as a structural defence for Dominant Concern #2.
        if document_context and document_context.whole_inject_docs:
            parts.append(self._render_whole_inject_documents(document_context))

        # 9. Footer.
        parts.append(_FOOTER)

        return "\n\n".join(parts)

    @staticmethod
    def _render_whole_inject_documents(document_context: DocumentContext) -> str:
        """Render attached small docs as a system-block section.

        Multiple docs are concatenated in attachment order with clear
        ``--- title ---`` delimiters so the model can tell them apart.
        """
        lines = ["Attached documents (full text):"]
        for doc in document_context.whole_inject_docs:
            lines.append(f"\n--- {doc.title} ---\n{doc.full_text}")
        return "\n".join(lines)

    @staticmethod
    def _render_attached_documents_synopsis(document_context: DocumentContext) -> str:
        """Render the "what's in scope" synopsis — Dominant Concern #2 defence.

        Per D-14-X-synopsis-source: auto-generated from filename + format +
        size + page/sheet count. Format:
        ``Attached documents: X.pdf (PDF, 187 pages, 84 KB); contracts.xlsx
        (XLSX, 3 sheets)``. Present every turn under retrieval, lists ALL
        attached documents regardless of which had chunks retrieved this
        turn. The model can then reason about coverage rather than mistaking
        retrieved fragments for the whole.
        """
        descriptors = [
            PromptBuilder._format_document_descriptor(doc)
            for doc in document_context.attached_documents
        ]
        return "Attached documents: " + "; ".join(descriptors)

    @staticmethod
    def _format_document_descriptor(doc: DocumentDescriptor) -> str:
        """Auto-generate the per-document one-liner.

        Examples:
        - ``tenancy.pdf (PDF, 187 pages, 84 KB)``
        - ``finance.xlsx (XLSX, 3 sheets)``
        - ``memo.txt (TXT, 2 KB)``
        """
        format_upper = doc.format.upper()
        size_part = ""
        if doc.size_bytes is not None and doc.size_bytes > 0:
            size_kb = max(1, doc.size_bytes // 1024)
            size_part = f", {size_kb} KB"

        if doc.format == "xlsx" and doc.sheet_names:
            n = len(doc.sheet_names)
            sheets_word = "sheet" if n == 1 else "sheets"
            return f"{doc.title} ({format_upper}, {n} {sheets_word}{size_part})"
        if doc.page_count is not None and doc.page_count > 0:
            pages_word = "page" if doc.page_count == 1 else "pages"
            return f"{doc.title} ({format_upper}, {doc.page_count} {pages_word}{size_part})"
        return f"{doc.title} ({format_upper}{size_part})"

    @staticmethod
    def _render_retrieved_document_chunks(document_context: DocumentContext) -> str:
        """Render retrieved document chunks with per-chunk citations.

        T15 (D-14-5 above-episodic rank). Each chunk's citation carries
        title + page/sheet/section when present so the model can cite
        sources back to the user. Multiple chunks render in the order
        retrieval returned them (closest first per cosine distance).
        """
        lines = ["Relevant excerpts from attached documents:"]
        for chunk in document_context.retrieved_chunks:
            citation_parts: list[str] = [chunk.title]
            if chunk.page is not None:
                citation_parts.append(f"page {chunk.page}")
            if chunk.sheet is not None:
                citation_parts.append(f"sheet '{chunk.sheet}'")
            if chunk.section is not None:
                citation_parts.append(f"section '{chunk.section}'")
            citation = " — ".join(citation_parts)
            lines.append(f"\n[{citation}]\n{chunk.text}")
        return "\n".join(lines)

    @staticmethod
    def _reductions(
        context: RetrievedContext,
        document_context: DocumentContext | None = None,
    ) -> list[tuple[RetrievedContext, DocumentContext | None]]:
        """Progressively-reduced contexts per the §5.3 + D-14-5 ladder.

        Order (lowest-priority dropped first; identity + constraints + skill
        index never drop — the §5.3 floor):

        1. Drop episodic.
        2. Drop document retrieved chunks (D-14-5: docs drop AFTER episodic
           but BEFORE worldview when over-budget). Only inserted when there
           are document chunks to drop; otherwise skipped so the cascade
           matches the Phase 1 3-stage shape for callers without documents.
        3. Drop worldview.
        4. Drop self-facts (all retrieved context cleared).

        Returns tuples of ``(RetrievedContext, DocumentContext | None)`` so
        :meth:`build` can pass both back through :meth:`_assemble`. When
        ``document_context`` is ``None`` or has no retrieved chunks, the
        document-context slot rides through unchanged and the ladder
        reduces to the Phase 1 3-stage shape (no behavioural regression).
        """
        stages: list[tuple[RetrievedContext, DocumentContext | None]] = []
        has_retrieved_docs = bool(
            document_context is not None and document_context.retrieved_chunks
        )
        docs_no_chunks: DocumentContext | None
        if has_retrieved_docs and document_context is not None:
            docs_no_chunks = document_context.model_copy(update={"retrieved_chunks": ()})
        else:
            docs_no_chunks = document_context

        # Stage 1: drop episodic.
        stages.append((context.model_copy(update={"episodic": []}), document_context))
        # Stage 2: also drop document chunks (D-14-5) — only when there's
        # something to drop, so the Phase 1 3-stage shape is preserved
        # for callers without documents.
        if has_retrieved_docs:
            stages.append((context.model_copy(update={"episodic": []}), docs_no_chunks))
        # Stage 3: also drop worldview.
        stages.append(
            (
                context.model_copy(update={"episodic": [], "worldview": []}),
                docs_no_chunks,
            )
        )
        # Stage 4: also drop self-facts (all retrieved context gone).
        stages.append(
            (
                context.model_copy(update={"episodic": [], "worldview": [], "self_facts": []}),
                docs_no_chunks,
            )
        )
        return stages

    @staticmethod
    def _token_total(messages: list[ConversationMessage]) -> int:
        """Estimate the prompt's token count via the shared cl100k_base encoder.

        Spec 13 T03 widened ``ConversationMessage.content`` to
        ``str | list[MessageContent]``. We narrow defensively to the
        text-only path here — token totals are used for compaction
        budgets that pre-date multimodal content, and the list-form
        path is gated separately by the vision dispatcher.
        """
        return sum(count_tokens(m.content) for m in messages if isinstance(m.content, str))
