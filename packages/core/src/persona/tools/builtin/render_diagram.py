"""``render_diagram`` built-in tool — persist Mermaid / Graphviz DOT source.

Spec 28 (§2.4, D-28-3 / D-28-4 client-side rendering). A first-class tool so the
model emits a diagram by calling ``render_diagram(source, format)`` directly
instead of fabricating matplotlib/graphviz Python through ``code_execution``
(slow, lossy text→image). The tool is **lenient**: it persists the diagram
*source text* as-is — there is NO server-side Mermaid/Graphviz parser or
binary (Spec 24 Reading-B: rendering deps stay out of persona-core). The web
renderer turns the source into SVG client-side (mermaid.js / @hpcc-js/wasm),
and malformed source surfaces as a client-side render error in the panel's raw
view — never a server dependency.

The persisted artifact carries a specific MIME type
(``text/vnd.mermaid`` / ``text/vnd.graphviz``, D-28-X-render-diagram-mime) so the
web normalizer dispatches to the right client renderer without sniffing.
``rendered_inline=True`` — small diagrams render inline above the file card
(the web applies the D-28-8 size threshold).

Like every byte-producing tool the ``persister`` is optional: ``None`` ⇒ the
source is returned in the result for the model to read, with no artifact
(graceful degrade for the CLI / persister-less path).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools.audit import ToolAuditEvent
from persona.tools.protocol import AsyncTool, tool

if TYPE_CHECKING:
    from persona.tools.audit import ToolAuditLogger
    from persona.tools.workspace_persister import WorkspacePersister

__all__ = ["make_render_diagram_tool"]

_logger = get_logger("tools.render_diagram")

#: Per-format media type + on-disk extension (D-28-X-render-diagram-mime).
_FORMAT_META: dict[str, tuple[str, str]] = {
    "mermaid": ("text/vnd.mermaid", "mmd"),
    "dot": ("text/vnd.graphviz", "dot"),
}

#: Defensive cap on source size — a diagram source is small; reject runaway
#: input before persisting (mirrors the text_diff / file size guards).
_MAX_SOURCE_CHARS = 100_000


def make_render_diagram_tool(
    *,
    persister: WorkspacePersister | None = None,
    audit_logger: ToolAuditLogger | None = None,
    persona_id: str | None = None,
) -> AsyncTool:
    """Build the ``render_diagram`` :class:`AsyncTool`.

    Args:
        persister: Optional :class:`WorkspacePersister` (Spec 28). When provided,
            the diagram source is persisted and surfaced as a
            :class:`PersistedArtifact` on :attr:`ToolResult.artifacts` (rendered
            inline client-side). ``None`` ⇒ the source is returned in the result
            for the model, with no artifact.
        audit_logger: Optional tool-audit sink. When provided, one
            :class:`ToolAuditEvent` (``action="write"``) is emitted per
            successful persist.
        persona_id: Persona identifier for audit records.

    Returns:
        An :class:`AsyncTool` named ``render_diagram``. Failures return
        ``ToolResult(is_error=True, ...)`` — never raise.
    """

    @tool(
        name="render_diagram",
        description=(
            "YOU CAN render diagrams. Use this tool to produce a flowchart, "
            "sequence diagram, graph, or any diagram — do not draw it with code "
            "or describe it: call this tool. Provide 'source' (the diagram text) "
            "and 'format' ('mermaid' for Mermaid syntax, 'dot' for Graphviz DOT). "
            "The diagram is rendered as a scalable SVG in the chat. Optional "
            "'title' captions it."
        ),
    )
    async def render_diagram(
        source: str,
        format: Literal["mermaid", "dot"],  # noqa: A002 — model-facing arg name
        title: str | None = None,
    ) -> ToolResult:
        if not source.strip():
            return ToolResult(
                tool_name="render_diagram",
                content="empty diagram source",
                is_error=True,
            )
        if len(source) > _MAX_SOURCE_CHARS:
            return ToolResult(
                tool_name="render_diagram",
                content=f"diagram source too large (> {_MAX_SOURCE_CHARS} chars)",
                is_error=True,
            )

        mime_type, ext = _FORMAT_META[format]
        summary = f"rendered {format} diagram" + (f": {title}" if title else "")
        data: dict[str, object] = {"format": format, "title": title}

        # No persister (CLI / persister-less path): return the source for the
        # model; nothing to render in a UI here. Graceful degrade.
        if persister is None:
            data["source"] = source
            return ToolResult(tool_name="render_diagram", content=summary, data=data)

        try:
            artifact = await persister.persist(
                source.encode("utf-8"),
                mime_type=mime_type,
                suggested_filename=f"diagram.{ext}",
            )
        except Exception as exc:  # noqa: BLE001 — any persist failure → structured result
            _logger.warning(
                "render_diagram persist failed",
                persona_id=persona_id or "<unknown>",
                format=format,
                reason=str(exc),
            )
            return ToolResult(
                tool_name="render_diagram",
                content=f"persist_failed: {exc}",
                is_error=True,
                data={"format": format},
            )

        # Diagrams render inline above the file card (web applies D-28-8 size cap).
        artifact = artifact.model_copy(update={"rendered_inline": True})

        if audit_logger is not None:
            audit_logger.emit(
                ToolAuditEvent(
                    timestamp=datetime.now(UTC),
                    persona_id=persona_id,
                    tool_name="render_diagram",
                    action="write",
                    resource=artifact.workspace_path,
                    metadata={"format": format, "bytes": str(artifact.size_bytes)},
                )
            )

        data["workspace_path"] = artifact.workspace_path
        return ToolResult(
            tool_name="render_diagram",
            content=summary,
            data=data,
            artifacts=(artifact,),
        )

    return render_diagram
