"""Spec 17 T07 — Spec 14 → Spec 17 tabular boundary verification.

§6 criterion #7 ("tabular boundary with Spec 14 holds"): the same
uploaded CSV is **read** by Spec 14 ("what columns are in this?") and
**analysed** by Spec 17 ("plot the trend") — one upload, two consumers,
identical bytes.

The T01 source audit (finding #2) documented the path-scheme divergence
that makes this non-trivial:

- **Spec 13 image_service** stores under
  ``<workspace>/<owner_id>/<persona_id>/uploads/<blake2b>.<ext>``
  (persona-scoped, content-addressed; the served-by-default upload path).
- **Spec 14 document_service** stores under
  ``<workspace>/persona_<persona_id>/conversations/<conversation_id>/documents/<doc_ref>.<ext>``
  (conversation-scoped, ``persona_`` prefix, doc_ref-named).

These are different directories. For Spec 17's sandbox to analyse a
Spec 14 CSV, the runtime stages the bytes via Spec 12's
``input_files: list[SandboxFile]`` (the same mechanism Spec 16 M1a uses
for skill supplements). The sandbox sees the bytes at
``/workspace/in/<rel-path>`` regardless of where they live on the host.

This test verifies the byte-equality round-trip:

1. POST a CSV to the Spec 14 upload endpoint.
2. Assert the file lands at Spec 14's documented path.
3. Read the disk bytes; assert they equal the upload payload.
4. Wrap the bytes in a ``SandboxFile`` (the shape T04b's augmented
   input-files provider would build); assert ``content_bytes`` round-trips
   intact.

The byte-equality assertion IS the tabular-boundary contract. Whatever
Spec 14 reads is whatever Spec 17 stages is whatever the model sees.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from persona.sandbox.result import SandboxFile
from persona.stores.document_store import DocumentStore
from persona_api.app import create_app
from persona_api.auth import AuthenticatedUser
from persona_api.config import APIConfig
from persona_api.middleware.rls_context import make_rls_engine
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from persona.schema.chunks import PersonaChunk
    from sqlalchemy import Engine
    from tests.conftest import HashEmbedder384


class _InMemoryBackend:
    """Minimal Backend Protocol stand-in for DocumentStore. Same shape as
    the in-memory backends in test_documents_rls.py + test_document_prompt_bound.py.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], list[PersonaChunk]] = {}

    def upsert(self, *, persona_id: str, store_kind: str, chunks: list[PersonaChunk]) -> None:
        key = (persona_id, store_kind)
        existing = self.store.setdefault(key, [])
        existing_ids = {c.id for c in chunks}
        kept = [c for c in existing if c.id not in existing_ids]
        kept.extend(chunks)
        self.store[key] = kept

    def query(
        self,
        *,
        persona_id: str,
        store_kind: str,
        text: str,  # noqa: ARG002
        top_k: int,
        where: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> list[PersonaChunk]:
        return list(self.store.get((persona_id, store_kind), []))[:top_k]

    def get_all(self, *, persona_id: str, store_kind: str) -> list[PersonaChunk]:
        return list(self.store.get((persona_id, store_kind), []))

    def delete_persona(self, persona_id: str, store_kind: str) -> None:
        self.store.pop((persona_id, store_kind), None)

    def delete_documents(self, *, persona_id: str, store_kind: str, ids: list[str]) -> None:
        key = (persona_id, store_kind)
        self.store[key] = [c for c in self.store.get(key, []) if c.id not in set(ids)]


pytestmark = pytest.mark.integration


_VALID_YAML = """\
schema_version: "1.0"
identity:
  name: Astrid
  role: Norwegian tenancy law assistant
  background: |
    Helps tenants understand husleieloven.
  language_default: en
  constraints: []
"""

# A small CSV with mixed dtypes — exercises the dtype-aware code path
# Spec 17's data_analysis SKILL.md teaches.
_CSV_PAYLOAD = (
    b"date,region,sales,note\n"
    b"2025-01-01,oslo,12500,launch\n"
    b"2025-02-01,oslo,13800,growth\n"
    b"2025-03-01,bergen,9100,steady\n"
    b"2025-04-01,bergen,11200,promo\n"
    b"2025-05-01,trondheim,8400,dip\n"
)


@pytest.fixture
def client(
    migrated_engine: Engine,  # noqa: ARG001 — ensures schema + grants
    embedder: HashEmbedder384,
    tmp_path: Path,
) -> Iterator[tuple[TestClient, str, Path]]:
    """Real FastAPI client + one user; yields (client, user_id, workspace_root)."""
    import os

    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set")

    workspace_root = tmp_path / "workspace"
    cfg = APIConfig(
        app_database_url=app_url,
        audit_root=str(tmp_path / "audit"),
        workspace_root=workspace_root,
    )
    app = create_app(cfg)

    async def _fake_verify(token: str) -> AuthenticatedUser:
        return AuthenticatedUser(id=token, email=None)

    user_id = "user_t07_a"
    document_store = DocumentStore(backend=_InMemoryBackend())  # type: ignore[arg-type]
    with TestClient(app) as c:
        app.state.verify_token = _fake_verify
        app.state.embedder = embedder
        # Drop the lifespan-installed TierRegistry so the persona-detail
        # capabilities surface doesn't lazily instantiate a real chat backend
        # (AuthenticationError("missing API key") on CI without ANTHROPIC_API_KEY).
        if hasattr(app.state, "tier_registry"):
            app.state.tier_registry = None
        # Document upload + cascade need the DocumentStore wired (test_documents_rls pattern).
        app.state.build_document_store = lambda: document_store
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
                {"i": user_id, "e": f"{user_id}@x.test"},
            )
        su.dispose()
        yield c, user_id, workspace_root
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        su.dispose()


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}"}


def _create_persona(c: TestClient, user_id: str) -> str:
    resp = c.post("/v1/personas", json={"yaml": _VALID_YAML}, headers=_auth(user_id))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _create_conversation(c: TestClient, user_id: str, persona_id: str) -> str:
    resp = c.post(
        f"/v1/personas/{persona_id}/conversations",
        json={},
        headers=_auth(user_id),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


class TestSpec14CsvLandsAtDocumentedPath:
    """Spec 14 D-14-X-scope-binding-discipline path: documents land at
    ``<workspace>/persona_<persona_id>/conversations/<conversation_id>/documents/<doc_ref>.<ext>``.
    The T01 audit finding #2 path. Asserting it here means future
    document_service drift trips a loud test failure surfacing the
    cross-spec impact.
    """

    def test_csv_upload_lands_at_conversation_scoped_path(
        self,
        client: tuple[TestClient, str, Path],
    ) -> None:
        c, uid, workspace_root = client
        pid = _create_persona(c, uid)
        cid = _create_conversation(c, uid, pid)

        resp = c.post(
            f"/v1/personas/{pid}/uploads",
            files={"file": ("sales-2025.csv", _CSV_PAYLOAD, "text/csv")},
            data={"conversation_id": cid},
            headers=_auth(uid),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        doc_ref = body["doc_ref"]
        workspace_path = body["workspace_path"]

        # T01 finding #2: the path is conversation-scoped with the
        # ``persona_`` prefix, NOT the persona-scoped ``uploads/`` path
        # Spec 13 images use. This pin is the cross-spec contract.
        assert workspace_path.startswith(f"persona_{pid}/conversations/{cid}/documents/")
        assert workspace_path.endswith(".csv")

        # The actual file is on disk at that path.
        absolute = workspace_root / workspace_path
        assert absolute.is_file(), f"expected file at {absolute}"
        # And the doc_ref appears in the path (deterministic).
        assert doc_ref in workspace_path


class TestSpec14SandboxByteEquality:
    """The bytes Spec 14 stores ARE the bytes Spec 17 stages. No
    transformation, no transcode, no normalisation. Whatever the user
    uploaded is what the analysis model sees inside the sandbox.
    """

    def test_disk_bytes_match_upload_payload(
        self,
        client: tuple[TestClient, str, Path],
    ) -> None:
        """The Spec 14 read path returns the exact upload bytes."""
        c, uid, workspace_root = client
        pid = _create_persona(c, uid)
        cid = _create_conversation(c, uid, pid)

        resp = c.post(
            f"/v1/personas/{pid}/uploads",
            files={"file": ("sales-2025.csv", _CSV_PAYLOAD, "text/csv")},
            data={"conversation_id": cid},
            headers=_auth(uid),
        )
        assert resp.status_code == 201, resp.text
        workspace_path = resp.json()["workspace_path"]
        absolute = workspace_root / workspace_path

        # The stored bytes equal the upload payload byte-for-byte.
        assert absolute.read_bytes() == _CSV_PAYLOAD

    def test_bytes_round_trip_through_sandbox_file_shape(
        self,
        client: tuple[TestClient, str, Path],
    ) -> None:
        """The Spec 14 bytes can be wrapped into a ``SandboxFile`` for
        Spec 17's input_files staging without modification.

        This is the **boundary contract**: Spec 17's runtime augmented
        input-files provider (T04b) reads bytes off disk and constructs a
        ``SandboxFile(path=..., content_bytes=..., size_bytes=..., media_type=...)``.
        The sandbox then sees those bytes at ``/workspace/in/<sf.path>``.
        Asserting the SandboxFile shape carries the bytes intact means
        Spec 17's sandbox is structurally able to read a Spec 14 CSV.
        """
        c, uid, workspace_root = client
        pid = _create_persona(c, uid)
        cid = _create_conversation(c, uid, pid)

        resp = c.post(
            f"/v1/personas/{pid}/uploads",
            files={"file": ("sales-2025.csv", _CSV_PAYLOAD, "text/csv")},
            data={"conversation_id": cid},
            headers=_auth(uid),
        )
        assert resp.status_code == 201, resp.text
        workspace_path = resp.json()["workspace_path"]
        absolute = workspace_root / workspace_path
        disk_bytes = absolute.read_bytes()

        # Construct the SandboxFile the runtime would build to stage the
        # dataset into a Spec 17 sandbox dispatch. The path the model
        # sees inside the sandbox is the workspace-relative path under
        # /workspace/in/.
        staged = SandboxFile(
            path="sales-2025.csv",
            content_bytes=disk_bytes,
            size_bytes=len(disk_bytes),
            media_type="text/csv",
        )
        # Byte-equality round-trips: upload → disk → SandboxFile.
        assert staged.content_bytes == _CSV_PAYLOAD
        assert staged.size_bytes == len(_CSV_PAYLOAD)
        # The model would access this file at /workspace/in/sales-2025.csv
        # via `pd.read_csv("/workspace/in/sales-2025.csv")` — exactly the
        # path the data_analysis SKILL.md teaches.


class TestSpec14Spec17PathSchemesDiverge:
    """Pin the path-scheme divergence T01 audit finding #2 documented.

    Spec 13 images use ``<owner>/<persona>/uploads/<blake2b>.<ext>``.
    Spec 14 docs use ``persona_<persona>/conversations/<conv>/documents/<doc_ref>.<ext>``.
    These are DIFFERENT directories; they coexist in the same workspace_root.

    The divergence is intentional (Spec 14 conversation-scoping +
    cascade-delete vs Spec 13 persona-scoping + content-addressing) but
    means cross-spec readers must use the right resolver per modality.
    """

    def test_doc_path_is_not_under_uploads_prefix(
        self,
        client: tuple[TestClient, str, Path],
    ) -> None:
        c, uid, workspace_root = client
        pid = _create_persona(c, uid)
        cid = _create_conversation(c, uid, pid)
        resp = c.post(
            f"/v1/personas/{pid}/uploads",
            files={"file": ("data.csv", _CSV_PAYLOAD, "text/csv")},
            data={"conversation_id": cid},
            headers=_auth(uid),
        )
        assert resp.status_code == 201, resp.text
        workspace_path = resp.json()["workspace_path"]
        # The doc path does NOT start with "uploads/" — that's the Spec 13
        # image namespace; documents have their own scheme.
        assert not workspace_path.startswith("uploads/")
        # It also does NOT start with the owner_id namespace Spec 13 uses.
        assert not workspace_path.startswith(uid + "/")
        # It DOES start with persona_<id>/ — the conversation-scoped scheme.
        assert workspace_path.startswith(f"persona_{pid}/")
