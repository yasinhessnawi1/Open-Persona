"""Conversation lifecycle + SSE chat (spec 08, T08, KEYSTONE 1).

The chat pathway: load the persona's conversation, drive
``ConversationLoop.turn`` (spec 05), stream its ``StreamChunk``s as SSE events,
and — only AFTER the final chunk — persist the new messages (incl. the
``channel`` passthrough, D-08-3), the compacted conversation state, the
``turn_log`` (T12), and the credits deduction (T12).

The persist-after-final discipline is load-bearing (research §3, D-05-12):
persistence runs in the normal flow after the generator's final yield, NEVER in
a ``finally`` — a client disconnect cancels the async generator mid-stream, and
a ``finally`` would persist a half-finished turn (state corruption). The async
generator simply suspends; persistence is skipped.

The ``ConversationLoop`` is built per-request by the runtime factory (T10),
which this service receives as an injected ``loop_builder`` so it stays testable
with a scripted backend.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast

from persona.errors import PersonaError, PersonaNotFoundError
from persona.logging import get_logger
from persona.schema.conversation import Conversation, ConversationMessage
from sqlalchemy import delete, func, insert, over, select, update

from persona_api.db.models import conversations as conversations_t
from persona_api.db.models import messages as messages_t
from persona_api.db.models import personas as personas_t
from persona_api.errors import ConversationNotFoundError
from persona_api.sandbox import (
    SandboxRequestContext,
    reset_sandbox_request_context,
    set_sandbox_request_context,
)
from persona_api.services import document_service, image_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from persona.backends import StreamChunk
    from persona.sandbox.result import SandboxFile
    from persona_runtime.agentic.events import RunEvent
    from persona_runtime.images import TurnImage
    from persona_runtime.loop import ConversationLoop
    from persona_runtime.prompt import DocumentContext
    from sqlalchemy import Connection, Engine

    from persona_api.editions import CreditsPolicy
    from persona_api.schemas import ChannelContext
    from persona_api.schemas import ImageRef as ImageRefSchema

    # The runtime factory (T10) builds a ConversationLoop for a persona under the
    # current request's RLS scope, given the persona_id.
    LoopBuilder = Callable[[str], Awaitable[ConversationLoop]]


__all__ = [
    "create_conversation",
    "delete_conversation",
    "get_conversation",
    "list_conversations",
    "set_title",
    "stream_chat",
]

_log = get_logger("api.chat")

# The message role, mirroring ConversationMessage.role + the messages_role_check
# DB CHECK constraint (the source of truth that makes the cast in _to_message
# sound).
Role = Literal["user", "assistant", "system", "tool"]

# A title builder turns the first user message into a short conversation title.
TitleBuilder = "Callable[[str], Awaitable[str]]"
_MAX_TITLE_LEN = 120

# Server-side cap on the last-message preview returned by the LIST endpoint, so
# the sidebar never has to ship/trim a full message body. Longer messages are
# truncated and get a trailing ellipsis (see _truncate_preview).
LAST_MESSAGE_PREVIEW_MAX_LEN = 120

# Defensive per-file cap on a document staged into the sandbox input mount
# (document-workspace cascade). Documents are already size-validated at upload,
# but this bounds the bytes we copy into a single turn's sandbox input set so a
# pathological doc can't blow up the input payload. Mirrors the image
# MAX_UPLOAD_BYTES (20 MiB).
MAX_STAGED_DOCUMENT_BYTES = 20 * 1024 * 1024


def create_conversation(*, rls_engine: Engine, owner_id: str, persona_id: str, title: str) -> str:
    """Create a conversation against a persona (RLS-scoped). Returns its id."""
    conv_id = f"conv_{uuid.uuid4().hex}"
    with rls_engine.begin() as conn:
        # Verify the persona is the caller's (RLS would hide it otherwise).
        exists = conn.execute(select(personas_t.c.id).where(personas_t.c.id == persona_id)).first()
        if exists is None:
            raise PersonaNotFoundError("persona not found", context={"id": persona_id})
        conn.execute(
            insert(conversations_t).values(
                id=conv_id, owner_id=owner_id, persona_id=persona_id, title=title
            )
        )
    return conv_id


def delete_conversation(*, rls_engine: Engine, conversation_id: str) -> None:
    """Delete a conversation (cascades to its messages + turn_logs via FK).

    RLS-scoped → a conversation that isn't the caller's is invisible and the
    delete matches no row → 404.
    """
    with rls_engine.begin() as conn:
        result = conn.execute(
            delete(conversations_t)
            .where(conversations_t.c.id == conversation_id)
            .returning(conversations_t.c.id)
        )
        if result.first() is None:
            raise ConversationNotFoundError(
                "conversation not found", context={"id": conversation_id}
            )


def set_title(*, rls_engine: Engine, conversation_id: str, title: str) -> None:
    """Set a conversation's title (RLS-scoped; used by the auto-title path)."""
    with rls_engine.begin() as conn:
        conn.execute(
            update(conversations_t)
            .where(conversations_t.c.id == conversation_id)
            .values(title=title)
        )


def list_conversations(*, rls_engine: Engine, limit: int, offset: int) -> list[dict[str, object]]:
    """List the caller's conversations (RLS-scoped), paginated.

    Each returned row carries the conversation columns PLUS two derived
    last-message fields — ``last_message_preview`` (the most recent message's
    text, already trimmed + truncated server-side) and ``last_message_role`` —
    so the web sidebar can render a real preview. Both are ``None`` for a
    conversation with no messages.

    The latest message per conversation is resolved WITHOUT an N+1 fan-out: a
    single ``ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY
    created_at DESC)`` window picks the newest ``messages`` row per
    conversation, and that subquery LEFT-JOINs onto the paginated
    conversations (so a conversation with zero messages still appears, with
    NULL preview fields). The whole statement runs in the one RLS-scoped
    transaction, so the ``messages`` rows it reads are constrained to the
    caller's tenant exactly like the conversations are — no cross-tenant leak.
    """
    # The "latest message per conversation" subquery: rank messages newest-first
    # within each conversation and keep only rank 1. ``created_at`` ties are
    # broken by ``id`` so the pick is deterministic.
    ranked = select(
        messages_t.c.conversation_id.label("conversation_id"),
        messages_t.c.content.label("content"),
        messages_t.c.role.label("role"),
        over(
            func.row_number(),
            partition_by=messages_t.c.conversation_id,
            order_by=(messages_t.c.created_at.desc(), messages_t.c.id.desc()),
        ).label("rn"),
    ).subquery("ranked_messages")
    latest = select(ranked).where(ranked.c.rn == 1).subquery("latest_message")

    with rls_engine.begin() as conn:
        rows = (
            conn.execute(
                select(
                    conversations_t,
                    latest.c.content.label("last_message_content"),
                    latest.c.role.label("last_message_role"),
                )
                .select_from(
                    conversations_t.join(
                        latest,
                        conversations_t.c.id == latest.c.conversation_id,
                        isouter=True,
                    )
                )
                .order_by(conversations_t.c.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
    out: list[dict[str, object]] = []
    for r in rows:
        row = dict(r)
        # Collapse the raw body to the truncated preview at the service boundary
        # so the route/response never sees a full message body.
        row["last_message_preview"] = _truncate_preview(
            cast("str | None", row.pop("last_message_content"))
        )
        out.append(row)
    return out


def _truncate_preview(content: str | None) -> str | None:
    """Trim + truncate a message body to the sidebar preview cap.

    Returns ``None`` for a missing body (a conversation with no messages).
    Collapses surrounding whitespace, then truncates to
    :data:`LAST_MESSAGE_PREVIEW_MAX_LEN` characters with a trailing ellipsis
    when the trimmed text overflows.
    """
    if content is None:
        return None
    trimmed = content.strip()
    if len(trimmed) <= LAST_MESSAGE_PREVIEW_MAX_LEN:
        return trimmed
    return trimmed[: LAST_MESSAGE_PREVIEW_MAX_LEN - 1].rstrip() + "…"


def get_conversation(*, rls_engine: Engine, conversation_id: str) -> dict[str, object]:
    """Return a conversation + its full message history (RLS-scoped → 404)."""
    with rls_engine.begin() as conn:
        conv = (
            conn.execute(select(conversations_t).where(conversations_t.c.id == conversation_id))
            .mappings()
            .first()
        )
        if conv is None:
            raise ConversationNotFoundError(
                "conversation not found", context={"id": conversation_id}
            )
        msgs = (
            conn.execute(
                select(messages_t)
                .where(messages_t.c.conversation_id == conversation_id)
                .order_by(messages_t.c.created_at.asc())
            )
            .mappings()
            .all()
        )
    out = dict(conv)
    out["messages"] = [dict(m) for m in msgs]
    return out


def _load_conversation(conn: Connection, conversation_id: str) -> Conversation:
    """Materialise a runtime Conversation from the DB rows (RLS-scoped)."""
    conv = (
        conn.execute(select(conversations_t).where(conversations_t.c.id == conversation_id))
        .mappings()
        .first()
    )
    if conv is None:
        raise ConversationNotFoundError("conversation not found", context={"id": conversation_id})
    msgs = (
        conn.execute(
            select(messages_t)
            .where(messages_t.c.conversation_id == conversation_id)
            .order_by(messages_t.c.created_at.asc())
        )
        .mappings()
        .all()
    )
    return Conversation(
        conversation_id=str(conv["id"]),
        persona_id=str(conv["persona_id"]),
        messages=[_to_message(dict(m)) for m in msgs],
        compacted_summary=str(conv["compacted_summary"]),
        compacted_up_to=int(conv["compacted_up_to"]),
    )


def _to_message(row: dict[str, object]) -> ConversationMessage:
    """Build a ConversationMessage from a DB row. ``role`` is constrained to the
    valid set by the ``messages_role_check`` DB CHECK, so the cast is sound."""
    role = cast("Role", str(row["role"]))
    created_at = cast("datetime", row["created_at"])
    return ConversationMessage(role=role, content=str(row["content"]), created_at=created_at)


def _sse(event: str, data: dict[str, object]) -> bytes:
    """Format one SSE event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def stream_chat(
    *,
    rls_engine: Engine,
    loop_builder: LoopBuilder,
    owner_id: str,
    conversation_id: str,
    user_message: str,
    channel: ChannelContext | None,
    credits_policy: CreditsPolicy,
    credits_per_turn: int = 1,
    title_builder: Callable[[str], Awaitable[str]] | None = None,
    images: list[ImageRefSchema] | None = None,
    turn_has_image: bool = False,
    document_context: DocumentContext | None = None,
    workspace_root: Path | None = None,
) -> AsyncIterator[bytes]:
    """Drive ConversationLoop.turn and stream SSE; persist after the final yield.

    Yields SSE frames (``chunk`` / ``done``). After the loop's final chunk — and
    ONLY on clean completion (a client disconnect cancels this generator and
    skips persistence) — the new messages, compaction state, channel, turn_log
    (the loop wrote it), and the credits deduction are committed (D-08-6: a
    failed/cancelled turn deducts nothing).

    On the FIRST turn of a conversation (no prior messages), an optional
    ``title_builder`` (the small tier) generates a short title from the first
    user message — best-effort: a failure leaves the default title untouched.

    Granular turn events (``tool_calling`` / ``tool_result`` as tools dispatch,
    plus the router's ``tier``) are surfaced via the loop's ``on_event``
    callback and emitted as SSE frames IN ORDER, interleaved with the text
    chunks, before the terminal ``done`` event. These reuse the SAME
    :class:`RunEvent` shapes as the run-viewer stream — one event vocabulary
    covers both (closes the gap spec 09 found).
    """
    # Load the conversation under the RLS scope (its own short transaction).
    with rls_engine.begin() as conn:
        conversation = _load_conversation(conn, conversation_id)
        prior_msg_count = len(conversation.messages)
    persona_id = conversation.persona_id
    is_first_turn = prior_msg_count == 0

    # Spec 12 T10: bind the per-request sandbox context for ``code_execution``.
    # The runtime factory reads the contextvar inside the tool factory closures
    # so we don't thread (owner_id, conversation_id) through every loop-builder
    # signature. The contextvar stays bound for the entire stream (including
    # loop.turn dispatches, where code_execution actually fires) and is reset
    # in the finally regardless of completion / cancellation.
    _sandbox_ctx_token = set_sandbox_request_context(
        SandboxRequestContext(owner_id=owner_id, conversation_id=conversation_id)
    )
    try:
        loop = await loop_builder(persona_id)

        # Image-workspace cascade (Part 1): resolve the inbound image refs to
        # runtime-layer TurnImage carriers (workspace_path + media_type + bytes)
        # so loop.turn can route them to BOTH the model (multimodal user
        # message) and the sandbox (deferred input files). Bytes come from the
        # existing upload-storage resolver under the per-(owner, persona)
        # workspace. ``images`` still flows separately to ``_persist_turn`` for
        # the messages.images JSONB column (unchanged).
        turn_images = _resolve_turn_images(
            workspace_root=workspace_root,
            owner_id=owner_id,
            persona_id=persona_id,
            images=images,
        )

        # Document-workspace cascade: stage the conversation's attached documents'
        # ORIGINAL bytes into the sandbox input mount so file_read /
        # code_execution can read the ACTUAL uploaded document (not just the
        # document_context synopsis, and not a stale file from another context).
        turn_documents = _resolve_turn_documents(
            workspace_root=workspace_root,
            persona_id=persona_id,
            conversation_id=conversation_id,
        )
        # file_read is a HOST-side tool scoped to ``<workspace_root>/<owner_id>/
        # <persona_id>`` (runtime_factory._build_file_sandbox_root_provider) — a
        # DIFFERENT subtree from the conversation-scoped document store
        # (``<workspace_root>/persona_<id>/conversations/<conv>/documents/``).
        # Staging onto ``deferred_input_files`` only reaches the REMOTE E2B
        # sandbox (where ``code_execution`` runs), never file_read's host root,
        # so ``file_read("uploads/<name>")`` 404'd. Mirror each staged document
        # into the file_read scoped root at ``uploads/<filename>`` so BOTH tools
        # read the SAME relative path. Per-(owner, persona) isolation is intact:
        # we only ever write under THIS request's scoped root, and only THIS
        # conversation's attached documents.
        _stage_documents_for_file_read(
            workspace_root=workspace_root,
            owner_id=owner_id,
            persona_id=persona_id,
            documents=turn_documents,
        )

        # The loop fires on_event synchronously between chunk yields; buffer the
        # events and flush them (in order) before the next chunk's frame, so the SSE
        # stream preserves the true interleaving. `tier` is captured for `done`.
        pending: list[RunEvent] = []
        tier = "frontier"  # fallback; replaced by the router's real choice via on_event
        # Spec 31 (D-31-1): the concise model-decision summary rides the tier
        # event; captured here and folded into `done` (None on rule-based turns).
        routing_summary: dict[str, object] | None = None

        async def _on_event(event: RunEvent) -> None:
            pending.append(event)

        def _drain() -> list[bytes]:
            nonlocal tier, routing_summary
            frames: list[bytes] = []
            for ev in pending:
                if ev.type == "tier":
                    tier = str(ev.data.get("tier", tier))
                    routing_summary = ev.data.get("routing")  # Spec 31; may be None
                    continue  # tier rides the `done` event, not its own SSE frame
                frames.append(_sse(ev.type, ev.data))
            pending.clear()
            return frames

        last_chunk: StreamChunk | None = None
        # ``turn_has_image`` rides as a keyword so the runtime loop's
        # T13-T09 vision pre-filter (Router._candidate_tiers) restricts the
        # candidate tier set to vision-capable backends when image refs are
        # present. The scripted test loops accept ``**_kwargs`` so this is
        # compatible with both the real loop and the spec-08 scripted loops.
        async for chunk in loop.turn(
            conversation,
            user_message,
            _on_event,
            turn_has_image=turn_has_image,
            images=turn_images or None,
            documents=turn_documents or None,
            document_context=document_context,
        ):
            last_chunk = chunk
            for frame in _drain():  # tool_calling / tool_result that fired before this chunk
                yield frame
            if chunk.delta:
                yield _sse("chunk", {"delta": chunk.delta, "is_final": chunk.is_final})
        for frame in _drain():  # any trailing events after the final chunk
            yield frame

        # ---- persist-after-final (only reached on clean completion) ----
        usage = last_chunk.usage if last_chunk is not None else None
        _persist_turn(
            rls_engine=rls_engine,
            conversation=conversation,
            prior_msg_count=prior_msg_count,
            channel=channel,
            images=images,
            tier=tier,
        )
        # Auto-title the conversation from its first user message (best-effort, small
        # tier). Failure leaves the default title — never breaks the turn.
        if is_first_turn and title_builder is not None:
            await _maybe_set_title(rls_engine, conversation_id, user_message, title_builder)
        # Deduct credits per successful turn (after the stream completes — D-08-6).
        credits_policy.deduct(
            rls_engine=rls_engine, user_id=owner_id, amount=credits_per_turn, reason="chat_turn"
        )
        done: dict[str, object] = {
            "usage": (
                {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens}
                if usage is not None
                else {}
            ),
            "tier": tier,  # the router's real choice for this turn (D-08 gap fix)
            "format_hints": {},  # D-08-3: the API echoes empty; connectors populate (spec 12)
        }
        # Spec 31 — SEPARATE, additive routing (D-31-1) + budget (D-31-2) fields.
        # `routing` rode the tier event; `budget` is read post-turn so the
        # session spend includes the turn just completed (D-31-X-session-spend).
        if routing_summary is not None:
            done["routing"] = routing_summary
        # `loop` is duck-typed (scripted test loops implement only `turn`); guard
        # the budget read so non-ConversationLoop builders stay back-compatible.
        snapshot_fn = getattr(loop, "budget_snapshot", None)
        budget = snapshot_fn() if callable(snapshot_fn) else None
        if budget is not None:
            done["budget"] = budget
        yield _sse("done", done)
    finally:
        reset_sandbox_request_context(_sandbox_ctx_token)


def _resolve_turn_images(
    *,
    workspace_root: Path | None,
    owner_id: str,
    persona_id: str,
    images: list[ImageRefSchema] | None,
) -> list[TurnImage]:
    """Resolve inbound image refs to runtime :class:`TurnImage` carriers.

    Reads each uploaded image's bytes from the persona workspace via the
    existing :func:`persona_api.services.image_service.fetch` resolver (the same
    path that backs ``GET /uploads/{ref}``), so the loop can route them to both
    the model and the sandbox. The bytes live exactly once under
    ``workspace_root/owner_id/persona_id`` (Spec 13 D-13-X-now option c); this
    is read-only resolution, never a second persisted copy.

    Returns an empty list when there are no images or no ``workspace_root`` is
    configured (CLI / test paths) — the text-only path is unaffected. A
    ref that cannot be resolved (deleted/cross-tenant) is skipped with a
    WARNING rather than failing the whole turn: the persisted ``images`` JSONB
    is still written by :func:`_persist_turn`, and a partial-vision turn beats a
    hard 500 mid-stream.

    Args:
        workspace_root: The per-deployment workspace root (``app.state``).
        owner_id: Authenticated tenant id (RLS scope).
        persona_id: Persona owning the conversation + the uploads.
        images: Inbound image refs from the chat body (may be ``None``).

    Returns:
        Resolved :class:`TurnImage` carriers in caller order (possibly empty).
    """
    if not images or workspace_root is None:
        return []
    # Local import: keeps the api-runtime import graph free of the runtime
    # package at module load + mirrors the lazy-import discipline elsewhere.
    from persona_runtime.images import TurnImage

    resolved: list[TurnImage] = []
    for ref in images:
        try:
            file_bytes, _media = image_service.fetch(
                workspace_root=workspace_root,
                owner_id=owner_id,
                persona_id=persona_id,
                ref=ref.workspace_path,
            )
        except PersonaError as exc:
            _log.warning(
                "uploaded image could not be resolved for the turn; skipping",
                workspace_path=ref.workspace_path,
                reason=str(exc),
            )
            continue
        resolved.append(
            TurnImage(
                workspace_path=ref.workspace_path,
                media_type=ref.media_type,
                content_bytes=file_bytes,
            )
        )
    return resolved


def _resolve_turn_documents(
    *,
    workspace_root: Path | None,
    persona_id: str,
    conversation_id: str,
) -> list[SandboxFile]:
    """Resolve the conversation's attached documents to sandbox input files.

    Document-workspace cascade: uploaded NON-image documents reached the model
    only as a ``document_context`` synopsis; the sandbox ``file_read`` /
    ``code_execution`` tools never saw the actual file (so ``file_read`` could
    surface a stale, unrelated file). This stages each attached document's
    ORIGINAL bytes as a :class:`SandboxFile` under the sandbox input mount at
    ``uploads/<filename>`` so the runtime loop appends it to
    ``deferred_input_files`` and the tools can read THIS file.

    Bytes are read via the existing document-store resolver
    (:func:`document_service.read_document_bytes`) — no new storage. A document
    that cannot be read (missing/oversize) is skipped with a WARNING rather than
    failing the turn; the model still has the synopsis via ``document_context``.

    Args:
        workspace_root: The per-deployment workspace root (``app.state``).
        persona_id: Persona owning the conversation + the documents.
        conversation_id: Conversation scope (documents are conversation-scoped).

    Returns:
        Resolved :class:`SandboxFile` carriers in workspace order (possibly
        empty — no documents, or no ``workspace_root`` on the CLI/test path).
    """
    if workspace_root is None:
        return []
    # Local import: keep the api module-load import graph free of the runtime/
    # core sandbox types (mirrors the lazy-import discipline above).
    from persona.sandbox.result import SandboxFile, guess_media_type  # noqa: PLC0415

    refs = document_service.list_for_conversation(
        sandbox_root=workspace_root,
        persona_id=persona_id,
        conversation_id=conversation_id,
    )
    if not refs:
        return []

    resolved: list[SandboxFile] = []
    for ref in refs:
        file_bytes = document_service.read_document_bytes(
            sandbox_root=workspace_root,
            persona_id=persona_id,
            conversation_id=conversation_id,
            doc_ref=ref.doc_ref,
        )
        if file_bytes is None:
            _log.warning(
                "uploaded document could not be resolved for the turn; skipping",
                doc_ref=ref.doc_ref,
                filename=ref.filename,
            )
            continue
        if len(file_bytes) > MAX_STAGED_DOCUMENT_BYTES:
            _log.warning(
                "uploaded document exceeds the sandbox staging cap; skipping",
                doc_ref=ref.doc_ref,
                size_bytes=len(file_bytes),
                max_bytes=MAX_STAGED_DOCUMENT_BYTES,
            )
            continue
        # Stage at a predictable, model-readable ``uploads/<filename>`` path so a
        # sandbox ``file_read("uploads/<filename>")`` finds THIS document.
        resolved.append(
            SandboxFile(
                path=f"uploads/{ref.filename}",
                content_bytes=file_bytes,
                size_bytes=len(file_bytes),
                media_type=guess_media_type(ref.filename),
            )
        )
    return resolved


def _stage_documents_for_file_read(
    *,
    workspace_root: Path | None,
    owner_id: str,
    persona_id: str,
    documents: list[SandboxFile],
) -> None:
    """Mirror staged documents into the HOST-side ``file_read`` scoped root.

    The ``code_execution`` tool reads the documents staged onto
    ``deferred_input_files`` because the runtime ships those bytes into the
    REMOTE sandbox's working directory (``/home/user/uploads/<name>`` on the
    hosted E2B substrate — relative ``uploads/<name>`` from CWD). The built-in
    ``file_read`` tool, by contrast, reads the LOCAL filesystem under its
    per-request scoped root ``<workspace_root>/<owner_id>/<persona_id>``
    (:func:`persona_api.services.runtime_factory.RuntimeFactory._build_file_sandbox_root_provider`)
    — a *different* subtree from the conversation-scoped document store. Without
    this mirror, ``file_read("uploads/<name>")`` resolves to a path that does
    not exist and returns ``FileNotFoundError``.

    This stages each document's bytes to
    ``<workspace_root>/<owner_id>/<persona_id>/uploads/<filename>`` so a
    ``file_read("uploads/<filename>")`` finds THIS conversation's uploaded
    document at the SAME relative path ``code_execution`` uses. The result is a
    single coherent path model — ``uploads/<filename>`` — across both tools.

    Isolation invariant (do NOT regress the just-landed security scoping): the
    target is resolved THROUGH :func:`resolve_sandbox_path` against the same
    per-(owner, persona) root file_read reads, so a pathological filename cannot
    escape the persona's subtree, and persona A never gains a path into persona
    B's (or another owner's) files. Only the CURRENT request's owner/persona
    root is ever written, and only the CURRENT conversation's attached
    documents (the ``documents`` already resolved for this turn).

    Best-effort: a write failure is logged and skipped (the model still has the
    ``document_context`` synopsis + the ``code_execution`` copy); a partial
    staging beats a hard turn failure.

    Args:
        workspace_root: The per-deployment workspace root (``app.state``). When
            ``None`` (CLI / test path) the file tools have no scoped provider
            anyway, so staging is a no-op.
        owner_id: Authenticated tenant id — the first scope segment.
        persona_id: Persona owning the conversation — the second scope segment.
        documents: The :class:`SandboxFile` carriers already resolved for this
            turn by :func:`_resolve_turn_documents` (path ``uploads/<filename>``,
            bytes in ``content_bytes``).
    """
    if workspace_root is None or not documents:
        return
    # Local import: keep the api module-load import graph free of the core
    # sandbox resolver (mirrors the lazy-import discipline elsewhere here).
    from persona.errors import SandboxViolationError  # noqa: PLC0415
    from persona.tools._sandbox import resolve_sandbox_path  # noqa: PLC0415

    # The EXACT root the file_read provider resolves at dispatch time
    # (runtime_factory._build_file_sandbox_root_provider). Keeping the two in
    # lockstep is the contract that makes ``file_read("uploads/<name>")`` work.
    file_read_root = workspace_root / owner_id / persona_id
    for sf in documents:
        if sf.content_bytes is None:
            continue
        try:
            target = resolve_sandbox_path(file_read_root, sf.path)
        except SandboxViolationError:
            _log.warning(
                "document path escapes the file_read scoped root; not mirrored",
                path=sf.path,
            )
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(sf.content_bytes)
        except OSError as exc:
            _log.warning(
                "could not mirror document into the file_read root; skipping",
                path=sf.path,
                exc_type=type(exc).__name__,
            )


def _persist_turn(
    *,
    rls_engine: Engine,
    conversation: Conversation,
    prior_msg_count: int,
    channel: ChannelContext | None,
    images: list[ImageRefSchema] | None = None,
    tier: str | None = None,
) -> None:
    """Insert the new messages + update compaction state (one RLS-scoped txn).

    The loop appended the user message + assistant response to ``conversation``
    in place (D-S05-4). We persist only the messages beyond ``prior_msg_count``,
    tagging the FIRST new (user) message with the ``channel`` passthrough and —
    if the inbound POST carried image refs (spec 13 T20, D-13-X-now option c) —
    the ``images`` JSONB column as ``[{"workspace_path", "media_type"}, ...]``
    in caller order (criterion #11).

    Spec 35 (D-35-2): the router's ``tier`` for this turn is written onto the
    ASSISTANT row(s) only (``tier_used``), so the per-message tier chip survives
    a reload. Non-assistant rows persist ``tier_used=NULL``.
    """
    new_messages = conversation.messages[prior_msg_count:]
    channel_json = channel.model_dump() if channel is not None else None
    images_json: list[dict[str, str]] | None = (
        [{"workspace_path": img.workspace_path, "media_type": img.media_type} for img in images]
        if images
        else None
    )
    now = datetime.now(UTC)
    with rls_engine.begin() as conn:
        for i, msg in enumerate(new_messages):
            is_first_user_msg = i == 0 and msg.role == "user"
            # Assign an EXPLICIT, strictly-increasing created_at per row instead
            # of leaning on the column's ``now()`` server default. Postgres'
            # ``now()`` is constant for the whole transaction, so every message
            # in one persisted turn would otherwise tie to the same instant —
            # leaving "the latest message" ambiguous (the LIST preview window in
            # list_conversations orders by created_at desc). A per-index
            # microsecond offset preserves the true user→assistant→tool insertion
            # order so the preview reflects the most recent turn deterministically.
            conn.execute(
                insert(messages_t).values(
                    id=f"msg_{uuid.uuid4().hex}",
                    conversation_id=conversation.conversation_id,
                    role=msg.role,
                    content=_persisted_content(msg.content),
                    created_at=now + timedelta(microseconds=i),
                    # Only the user message carries the inbound channel context.
                    channel=channel_json if is_first_user_msg else None,
                    # Only the first user message carries the inbound image refs
                    # (assistant + tool messages persist images=NULL — the response
                    # itself never carries inbound image refs).
                    images=images_json if is_first_user_msg else None,
                    # Spec 35 D-35-2: the routing tier rides the assistant row(s)
                    # only; user/tool rows persist NULL.
                    tier_used=tier if msg.role == "assistant" else None,
                )
            )
        conn.execute(
            update(conversations_t)
            .where(conversations_t.c.id == conversation.conversation_id)
            .values(
                compacted_summary=conversation.compacted_summary,
                compacted_up_to=conversation.compacted_up_to,
                updated_at=now,
            )
        )


def _persisted_content(content: object) -> str:
    """Reduce a ``ConversationMessage.content`` to the TEXT column shape.

    The DB ``messages.content`` column is ``TEXT``. The runtime widened
    :class:`ConversationMessage.content` to ``str | list[MessageContent]``
    (Spec 13 T03), but at the API persistence boundary we collapse the
    list form back to the text body — image refs are persisted on the
    sibling ``images`` JSONB column (D-13-X-now option c). For a list
    payload we concatenate the :class:`TextContent` blocks (in order)
    and ignore image blocks; for a bare ``str`` we pass through.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Lazy import: the dominant call path (text-only) never touches this
        # branch, so we avoid the import cost at module load.
        from persona.schema.content import TextContent  # noqa: PLC0415

        return "".join(block.text for block in content if isinstance(block, TextContent))
    # Defensive fallback — should never fire under ConversationMessage's
    # ``extra="forbid"`` validation contract.
    return str(content)


async def _maybe_set_title(
    rls_engine: Engine,
    conversation_id: str,
    first_message: str,
    title_builder: Callable[[str], Awaitable[str]],
) -> None:
    """Generate + persist a short title from the first message. Best-effort: any
    failure (model error, timeout) is logged and swallowed — the conversation
    keeps its default title rather than breaking the turn."""
    try:
        title = (await title_builder(first_message)).strip()
        if title:
            set_title(
                rls_engine=rls_engine, conversation_id=conversation_id, title=title[:_MAX_TITLE_LEN]
            )
    except Exception as exc:  # noqa: BLE001 — auto-title must never break a chat turn
        _log.warning("auto-title failed for {cid}: {err}", cid=conversation_id, err=str(exc))
