"""Unit tests for the Spec 28 ``render_diagram`` tool (B3, client-side)."""

from __future__ import annotations

import pytest
from persona.schema.tools import PersistedArtifact
from persona.tools.audit import MemoryToolAuditLogger
from persona.tools.builtin.render_diagram import make_render_diagram_tool


class _FakePersister:
    def __init__(self, *, side_effect: BaseException | None = None) -> None:
        self._side_effect = side_effect
        self.calls: list[dict[str, object]] = []

    async def persist(
        self, data: bytes, *, mime_type: str, suggested_filename: str
    ) -> PersistedArtifact:
        self.calls.append(
            {"data": data, "mime_type": mime_type, "suggested_filename": suggested_filename}
        )
        if self._side_effect is not None:
            raise self._side_effect
        return PersistedArtifact(
            workspace_path=f"uploads/{suggested_filename}",
            mime_type=mime_type,
            size_bytes=len(data),
        )


class TestRenderDiagramFactory:
    def test_named_render_diagram(self) -> None:
        tool = make_render_diagram_tool()
        assert tool.name == "render_diagram"
        assert tool.description

    def test_schema_exposes_source_and_format(self) -> None:
        schema = make_render_diagram_tool().parameters_schema
        assert "source" in schema["properties"]
        assert "format" in schema["properties"]
        # format is a Literal → enum in the generated schema.
        assert schema["properties"]["format"].get("enum") == ["mermaid", "dot"]


class TestRenderDiagramMermaid:
    @pytest.mark.asyncio
    async def test_persists_mermaid_source_with_specific_mime(self) -> None:
        persister = _FakePersister()
        tool = make_render_diagram_tool(persister=persister)
        result = await tool.execute(source="graph TD; A-->B", format="mermaid", title="Flow")
        assert result.is_error is False
        assert len(result.artifacts) == 1
        art = result.artifacts[0]
        assert art.mime_type == "text/vnd.mermaid"
        assert art.workspace_path.endswith(".mmd")
        assert art.rendered_inline is True
        # Lenient: the raw source is persisted unchanged (no server-side parse).
        assert persister.calls[0]["data"] == b"graph TD; A-->B"
        assert result.data["format"] == "mermaid"
        assert result.data["title"] == "Flow"


class TestRenderDiagramDot:
    @pytest.mark.asyncio
    async def test_persists_dot_source_with_specific_mime(self) -> None:
        persister = _FakePersister()
        tool = make_render_diagram_tool(persister=persister)
        result = await tool.execute(source="digraph { a -> b }", format="dot")
        assert result.is_error is False
        art = result.artifacts[0]
        assert art.mime_type == "text/vnd.graphviz"
        assert art.workspace_path.endswith(".dot")


class TestRenderDiagramEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_source_is_error(self) -> None:
        tool = make_render_diagram_tool(persister=_FakePersister())
        result = await tool.execute(source="   ", format="mermaid")
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_oversize_source_is_error(self) -> None:
        tool = make_render_diagram_tool(persister=_FakePersister())
        result = await tool.execute(source="x" * 100_001, format="mermaid")
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_no_persister_returns_source_no_artifact(self) -> None:
        tool = make_render_diagram_tool()
        result = await tool.execute(source="graph TD; A-->B", format="mermaid")
        assert result.is_error is False
        assert result.artifacts == ()
        assert result.data["source"] == "graph TD; A-->B"

    @pytest.mark.asyncio
    async def test_persist_failure_surfaces_structured_error(self) -> None:
        tool = make_render_diagram_tool(persister=_FakePersister(side_effect=OSError("disk full")))
        result = await tool.execute(source="graph TD; A-->B", format="mermaid")
        assert result.is_error is True
        assert "persist_failed" in result.content

    @pytest.mark.asyncio
    async def test_audit_emitted_on_success(self) -> None:
        audit = MemoryToolAuditLogger()
        tool = make_render_diagram_tool(
            persister=_FakePersister(), audit_logger=audit, persona_id="astrid"
        )
        await tool.execute(source="graph TD; A-->B", format="mermaid")
        assert len(audit.events) == 1
        assert audit.events[0].tool_name == "render_diagram"
        assert audit.events[0].metadata["format"] == "mermaid"


class TestCatalogEntry:
    def test_render_diagram_in_catalog(self) -> None:
        from persona.tools.catalog import known_tool_names

        assert "render_diagram" in known_tool_names()
