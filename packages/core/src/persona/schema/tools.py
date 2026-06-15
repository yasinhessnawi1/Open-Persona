"""Tool protocol and call/result types — definitions only.

Spec 03 ships the concrete tools (web_search, web_fetch, file_*, MCP client).
Spec 01 ships only the minimum surface so callers can type against the
protocol and the YAML allow-list can reference tool names that exist.

Design notes:
- ``Tool`` is a Protocol (PEP 544) marked ``@runtime_checkable`` so the
  toolbox can answer ``isinstance(obj, Tool)`` at registration time.
- The surface is deliberately small (``name``, ``description``,
  ``parameters_schema``, ``__call__``). Spec 03 adds decorators, registry,
  sandboxing, and async variants without breaking this protocol.
- ``ToolCall`` and ``ToolResult`` are frozen Pydantic models so they cross
  the runtime boundary cleanly (the model emits structured tool calls; the
  toolbox produces structured results).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["PersistedArtifact", "Tool", "ToolCall", "ToolResult"]


class ToolCall(BaseModel):
    """A structured request from the model to invoke a tool.

    Attributes:
        name: Tool name (matches the persona YAML's ``tools`` allow-list).
        args: Keyword arguments to pass to the tool. JSON-serialisable.
        call_id: Provider-supplied identifier so the matching ``ToolResult``
            can be correlated. Empty string if the provider doesn't supply one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    call_id: str = ""


class PersistedArtifact(BaseModel):
    """A durable byte-output a tool persisted to the persona workspace (Spec 28).

    Produced by a :class:`persona.tools.workspace_persister.WorkspacePersister`
    when a byte-producing tool (``generate_image`` / ``file_write`` /
    ``render_diagram``) or the code-execution sandbox surfaces a file. Carried
    on :attr:`ToolResult.artifacts` so the web layer can render an inline file
    card + the right-panel renderer (Spec 28 §2.2/§2.3).

    The shape is storage-agnostic on purpose (D-28-X-persisted-artifact-shape):
    ``workspace_path`` is the workspace-relative reference the existing
    ``GET /v1/personas/{id}/uploads/{ref}`` route already serves, and the
    download URL is *derived* from it at the API/web boundary (D-28-10) rather
    than stored here — so a future S3 backend (v0.3) implements the same
    Protocol without changing this model.

    Attributes:
        workspace_path: Workspace-relative path (e.g. ``"uploads/<hash>.png"``).
        mime_type: IANA media type of the bytes (drives the renderer dispatch;
            ``render_diagram`` uses ``text/vnd.mermaid`` / ``text/vnd.graphviz``
            per D-28-X-render-diagram-mime).
        size_bytes: Size of the persisted bytes.
        rendered_inline: Frontend hint — render inline (image thumbnail / inline
            SVG) above the file card vs. card-only. Defaults to ``False``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_path: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    rendered_inline: bool = False


class ToolResult(BaseModel):
    """A structured response from a tool execution.

    Attributes:
        tool_name: The name of the tool that produced this result.
        content: Free-form textual content (the model reads this).
        call_id: Echoes ``ToolCall.call_id`` so the model can pair them.
        is_error: True if the tool raised; ``content`` then contains the
            error description. The runtime feeds these back to the model so
            it can recover (try different args, give up, ask the user).
        metadata: Arbitrary string-keyed metadata (latency, source URL, etc.).
        data: Structured data for programmatic consumers (e.g.,
            ``web_search`` returns ``data={"results": [...]}`` while
            ``content`` is the human-readable summary). Added in spec 03
            (D-03-3); ``None`` for tools that produce only text.
        truncated: True when the tool truncated its result to fit a budget
            (e.g., ``web_fetch`` past ``max_chars``, ``file_read`` past 1 MB).
            Added in spec 03 (D-03-3).
        artifacts: Durable byte-outputs the tool persisted to the workspace
            (Spec 28, D-28-X-persisted-artifact-shape). Default-empty tuple so
            tools that produce no files serialise byte-identically to the
            pre-Spec-28 shape (backward-compat / acceptance criterion #9). The
            web layer renders each as an inline file card + right-panel content.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str
    content: str
    call_id: str = ""
    is_error: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)
    # Spec 03 — D-03-3. Additive optional fields; failure still expressed via
    # is_error + content, not a separate error channel.
    data: dict[str, Any] | None = None
    truncated: bool = False
    # Spec 28 — D-28-X-persisted-artifact-shape. One typed field grouping all
    # persisted byte-outputs; default-empty preserves the old serialised shape.
    artifacts: tuple[PersistedArtifact, ...] = ()


@runtime_checkable
class Tool(Protocol):
    """The minimum surface a tool implementation must expose.

    Concrete tools land in spec 03. This Protocol exists in spec 01 so
    callers (and the YAML allow-list mechanism) can type against it.

    Implementations are duck-typed — no inheritance required. The protocol
    is intentionally narrow so spec 03 can add features (async ``__call__``,
    streaming, capability flags) by introducing a more specific protocol
    (``StreamingTool``, ``AsyncTool``) rather than enlarging this one.
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]

    def __call__(self, **kwargs: Any) -> ToolResult:  # noqa: ANN401
        """Execute the tool with the given keyword arguments.

        ``**kwargs: Any`` is intentional: tool arguments are typed by
        ``parameters_schema`` (a JSON-schema-style dict), not by Python types.
        Concrete tools in spec 03 may narrow this at the implementation site.

        Returns:
            A :class:`ToolResult`. Failed executions must return a result
            with ``is_error=True`` rather than raising — the runtime feeds
            errors back to the model as structured results.
        """
        ...
