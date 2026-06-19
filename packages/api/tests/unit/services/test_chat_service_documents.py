"""Document-workspace cascade — chat_service document resolution + forwarding.

Uploaded NON-image documents reached the model only as a ``document_context``
synopsis; the sandbox ``file_read`` / ``code_execution`` tools never saw the
actual file (so ``file_read`` could surface a stale, unrelated README from
another context). Two contracts close that gap:

* ``_resolve_turn_documents`` reads each attached document's ORIGINAL bytes from
  the conversation's documents directory and builds :class:`SandboxFile`
  carriers staged at ``uploads/<filename>``.
* ``stream_chat`` forwards the resolved documents to ``loop.turn(documents=...)``
  so the loop stages them onto ``deferred_input_files`` and the sandbox tools
  read THIS document.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from persona.documents.ingest import IngestStrategy
from persona.schema.conversation import Conversation
from persona_api.services import chat_service
from persona_api.services.chat_service import _resolve_turn_documents
from persona_api.services.document_service import DOCUMENT_DIR_NAME, DocumentRef

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from persona.backends import StreamChunk
    from persona_runtime.agentic.events import RunEvent

_PERSONA = "astrid"
_CONV = "c1"
_DOC_BODY = b"# Polly's Layout Test\n\nThis is the ACTUAL uploaded document.\n"


def _write_document(
    workspace_root: Path,
    *,
    doc_ref: str = "pollys_layout_test",
    filename: str = "pollys-layout-test.md",
    body: bytes = _DOC_BODY,
) -> DocumentRef:
    """Lay down an attached document the way document_service.upload does."""
    base = workspace_root / f"persona_{_PERSONA}" / "conversations" / _CONV / DOCUMENT_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    original = base / f"{doc_ref}.md"
    original.write_bytes(body)
    relative_path = f"persona_{_PERSONA}/conversations/{_CONV}/{DOCUMENT_DIR_NAME}/{doc_ref}.md"
    ref = DocumentRef(
        doc_ref=doc_ref,
        filename=filename,
        title=filename,
        format="md",
        workspace_path=relative_path,
        strategy=IngestStrategy.WHOLE_INJECT,
        token_count=10,
        size_bytes=len(body),
    )
    sidecar = original.with_suffix(original.suffix + ".meta.json")
    sidecar.write_text(ref.model_dump_json())
    return ref


class TestResolveTurnDocuments:
    def test_resolves_original_bytes_at_uploads_path(self, tmp_path: Path) -> None:
        _write_document(tmp_path)

        staged = _resolve_turn_documents(
            workspace_root=tmp_path, persona_id=_PERSONA, conversation_id=_CONV
        )

        assert len(staged) == 1
        sf = staged[0]
        assert sf.path == "uploads/pollys-layout-test.md"
        assert sf.content_bytes == _DOC_BODY
        assert sf.media_type == "text/markdown"

    def test_no_workspace_root_returns_empty(self) -> None:
        assert (
            _resolve_turn_documents(workspace_root=None, persona_id=_PERSONA, conversation_id=_CONV)
            == []
        )

    def test_no_documents_returns_empty(self, tmp_path: Path) -> None:
        assert (
            _resolve_turn_documents(
                workspace_root=tmp_path, persona_id=_PERSONA, conversation_id=_CONV
            )
            == []
        )

    def test_oversize_document_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_document(tmp_path, body=b"x" * 1024)
        monkeypatch.setattr(chat_service, "MAX_STAGED_DOCUMENT_BYTES", 10)

        assert (
            _resolve_turn_documents(
                workspace_root=tmp_path, persona_id=_PERSONA, conversation_id=_CONV
            )
            == []
        )


class _CapturingLoop:
    """Scripted loop that records the ``documents`` kwarg it received."""

    def __init__(self) -> None:
        self.documents_seen: list[object] | None = None

    async def turn(
        self,
        conversation: Conversation,
        user_message: str,
        on_event: Callable[[RunEvent], Awaitable[None]] | None = None,  # noqa: ARG002
        *,
        turn_has_image: bool = False,  # noqa: ARG002
        images: list[object] | None = None,  # noqa: ARG002
        documents: list[object] | None = None,
        document_context: object = None,  # noqa: ARG002
    ) -> AsyncIterator[StreamChunk]:
        from persona.backends import StreamChunk
        from persona.schema.conversation import ConversationMessage

        self.documents_seen = documents
        now = datetime.now(UTC)
        conversation.messages.append(
            ConversationMessage(role="user", content=user_message, created_at=now)
        )
        conversation.messages.append(
            ConversationMessage(role="assistant", content="ok", created_at=now)
        )
        yield StreamChunk(delta="ok", is_final=False)
        yield StreamChunk(delta="", is_final=True)


class _NoopCredits:
    def deduct(self, **_kwargs: object) -> None: ...


@pytest.mark.asyncio
async def test_stream_chat_forwards_resolved_documents_to_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_document(tmp_path)
    captured = _CapturingLoop()

    monkeypatch.setattr(
        chat_service,
        "_load_conversation",
        lambda _conn, _cid: Conversation(conversation_id=_CONV, persona_id=_PERSONA, messages=[]),
    )
    monkeypatch.setattr(chat_service, "_persist_turn", lambda **_kwargs: None)

    class _FakeConn:
        def __enter__(self) -> _FakeConn:
            return self

        def __exit__(self, *_a: object) -> None: ...

    class _FakeEngine:
        def begin(self) -> _FakeConn:
            return _FakeConn()

    async def _build_loop(_pid: str) -> _CapturingLoop:
        return captured

    frames = [
        frame
        async for frame in chat_service.stream_chat(
            rls_engine=_FakeEngine(),  # type: ignore[arg-type]
            loop_builder=_build_loop,  # type: ignore[arg-type]
            owner_id="user_1",
            conversation_id=_CONV,
            user_message="summarise the doc",
            channel=None,
            credits_policy=_NoopCredits(),  # type: ignore[arg-type]
            workspace_root=tmp_path,
        )
    ]

    assert frames
    assert captured.documents_seen is not None
    assert len(captured.documents_seen) == 1
    sf = captured.documents_seen[0]
    assert sf.path == "uploads/pollys-layout-test.md"  # type: ignore[attr-defined]
    assert sf.content_bytes == _DOC_BODY  # type: ignore[attr-defined]
