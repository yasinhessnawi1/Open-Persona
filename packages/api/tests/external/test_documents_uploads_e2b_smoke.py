"""Live E2B smoke test for the uploaded-document path reconciliation.

Root-cause regression guard (uploads-readable-runtime): an uploaded NON-image
document must be readable by BOTH tools under ONE coherent path model
(``uploads/<filename>``):

* ``code_execution`` (the REMOTE E2B sandbox): the document's ORIGINAL bytes,
  staged onto ``deferred_input_files`` and shipped into the sandbox by the
  hosted bridge, must appear in the sandbox working directory so the model's
  ``os.listdir("uploads")`` / ``open("uploads/<name>")`` finds it. The faked
  unit tests could not catch the live gap — the hosted bridge writes input
  files to ``/home/user/<path>`` while the sandbox CWD is ``/home/user`` and
  the produced-files verification block points the model at ``/workspace/out``
  (a DIFFERENT dir). This test runs a REAL sandbox to prove the staged document
  lands where ``code_execution`` actually looks (relative ``uploads/<name>``).

* ``file_read`` (the HOST-side scoped root) is covered by the unit/contract
  suite (``test_chat_service_documents.py``) because it reads the LOCAL
  filesystem — no live substrate needed.

**Cost discipline:** one fresh stateless sandbox held for ~3-5s
(~$0.0002 on E2B Hobby). Skipped by default (``external`` marker); run with::

    uv run pytest -m external \
        packages/api/tests/external/test_documents_uploads_e2b_smoke.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from persona_api.services.chat_service import _resolve_turn_documents
from persona_api.services.document_service import DOCUMENT_DIR_NAME, DocumentRef

pytestmark = [pytest.mark.external, pytest.mark.integration]

_PERSONA = "astrid"
_CONV = "c1"
_FILENAME = "README.md"
_DOC_BODY = b"# Project README\n\nThe answer to the question is 42.\n"


def _e2b_key_set() -> bool:
    return bool(os.environ.get("E2B_API_KEY", "").strip())


def _write_stored_document(workspace_root: Path) -> None:
    """Lay down a stored document the way ``document_service.upload`` does."""
    from persona.documents.ingest import IngestStrategy

    base = workspace_root / f"persona_{_PERSONA}" / "conversations" / _CONV / DOCUMENT_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    doc_ref = "readme-abcd1234"
    (base / f"{doc_ref}.md").write_bytes(_DOC_BODY)
    ref = DocumentRef(
        doc_ref=doc_ref,
        filename=_FILENAME,
        title=_FILENAME,
        format="md",
        workspace_path=f"persona_{_PERSONA}/conversations/{_CONV}/{DOCUMENT_DIR_NAME}/{doc_ref}.md",
        strategy=IngestStrategy.WHOLE_INJECT,
        token_count=10,
        size_bytes=len(_DOC_BODY),
    )
    (base / f"{doc_ref}.md.meta.json").write_text(ref.model_dump_json())


@pytest.mark.asyncio
@pytest.mark.skipif(not _e2b_key_set(), reason="E2B_API_KEY not set")
async def test_uploaded_document_readable_in_live_e2b_sandbox(tmp_path: Path) -> None:
    """The staged document is visible + readable in a REAL E2B sandbox CWD."""
    from persona_api.sandbox import HostedSandbox

    # 1. Stored document at the real conversation-scoped storage path.
    _write_stored_document(tmp_path)

    # 2. Resolve to SandboxFile carriers exactly as stream_chat does.
    staged = _resolve_turn_documents(
        workspace_root=tmp_path, persona_id=_PERSONA, conversation_id=_CONV
    )
    assert len(staged) == 1
    assert staged[0].path == f"uploads/{_FILENAME}"

    # 3. Run a REAL code_execution that lists the working dir + reads the doc.
    probe = (
        "import os\n"
        "print('CWD', os.getcwd())\n"
        "print('UPLOADS', os.listdir('uploads'))\n"
        "print('BODY', open('uploads/README.md').read())\n"
    )
    sandbox = HostedSandbox()
    try:
        result = await sandbox.execute(probe, input_files=list(staged))
    finally:
        await sandbox.aclose()

    assert result.outcome == "ok", result.stderr
    # The README is present in the dir code_execution actually lists...
    assert "README.md" in result.stdout
    # ...and its ACTUAL bytes are readable (not a stale/other file).
    assert "The answer to the question is 42." in result.stdout
