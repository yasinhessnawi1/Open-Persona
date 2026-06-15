"""Tests for the Spec 28 ``WorkspacePersister`` port + ``PersistedArtifact``."""

# ruff: noqa: ARG002 — fake-persister signature parity
from __future__ import annotations

import pytest
from persona.schema.tools import PersistedArtifact
from persona.tools.workspace_persister import WorkspacePersister
from pydantic import ValidationError


class TestPersistedArtifact:
    """The frozen artifact identity carried on ToolResult.artifacts."""

    def test_minimal_construction_defaults_rendered_inline_false(self) -> None:
        a = PersistedArtifact(
            workspace_path="uploads/abc123.png",
            mime_type="image/png",
            size_bytes=42,
        )
        assert a.workspace_path == "uploads/abc123.png"
        assert a.mime_type == "image/png"
        assert a.size_bytes == 42
        assert a.rendered_inline is False

    def test_frozen(self) -> None:
        a = PersistedArtifact(workspace_path="uploads/x.txt", mime_type="text/plain", size_bytes=1)
        with pytest.raises(ValidationError):
            a.workspace_path = "uploads/y.txt"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            PersistedArtifact(  # type: ignore[call-arg]
                workspace_path="uploads/x", mime_type="text/plain", size_bytes=1, download_url="/no"
            )

    def test_negative_size_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PersistedArtifact(workspace_path="uploads/x", mime_type="text/plain", size_bytes=-1)

    def test_round_trips_through_json(self) -> None:
        a = PersistedArtifact(
            workspace_path="uploads/d.svg",
            mime_type="text/vnd.mermaid",
            size_bytes=128,
            rendered_inline=True,
        )
        restored = PersistedArtifact.model_validate_json(a.model_dump_json())
        assert restored == a


class TestWorkspacePersisterProtocol:
    """The port is runtime_checkable and structurally matched."""

    def test_class_with_persist_satisfies(self) -> None:
        class Fake:
            async def persist(
                self, data: bytes, *, mime_type: str, suggested_filename: str
            ) -> PersistedArtifact:
                return PersistedArtifact(
                    workspace_path=f"uploads/{suggested_filename}",
                    mime_type=mime_type,
                    size_bytes=len(data),
                )

        assert isinstance(Fake(), WorkspacePersister)

    def test_class_without_persist_fails(self) -> None:
        class NotAPersister:
            pass

        assert not isinstance(NotAPersister(), WorkspacePersister)

    @pytest.mark.asyncio
    async def test_fake_persist_returns_artifact(self) -> None:
        class Fake:
            async def persist(
                self, data: bytes, *, mime_type: str, suggested_filename: str
            ) -> PersistedArtifact:
                return PersistedArtifact(
                    workspace_path="uploads/report.md",
                    mime_type=mime_type,
                    size_bytes=len(data),
                    rendered_inline=False,
                )

        persister: WorkspacePersister = Fake()
        art = await persister.persist(
            b"hello", mime_type="text/markdown", suggested_filename="report.md"
        )
        assert art.workspace_path == "uploads/report.md"
        assert art.size_bytes == 5
        assert art.mime_type == "text/markdown"
