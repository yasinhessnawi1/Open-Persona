"""Data types crossing the backend boundary.

Frozen Pydantic v2 models with ``extra="forbid"`` everywhere — these
shapes are returned by :class:`persona.backends.protocol.ChatBackend`
implementations and consumed by the runtime (spec 05), the audit log
(future), and the HTTP API (spec 08). See ``docs/specs/spec_02/decisions.md``
D-02-2 for the Pydantic-over-dataclass rationale.

Spec 02 §4.1 enumerates these shapes. The validators here enforce the
invariants the spec describes (e.g., ``TokenUsage.total_tokens`` consistency).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from persona.schema.tools import ToolCall  # noqa: TC001 — Pydantic needs runtime ref

if TYPE_CHECKING:
    from persona.schema.tools import Tool

__all__ = [
    "ChatResponse",
    "StreamChunk",
    "ToolCallDelta",
    "ToolSpec",
    "TokenUsage",
    "tool_spec_from_tool",
]


class TokenUsage(BaseModel):
    """Token accounting for a single backend call.

    Attributes:
        prompt_tokens: Tokens consumed by the prompt (system + history + user).
        completion_tokens: Tokens emitted by the model in the response.
        total_tokens: Sum of the above. Validated to equal
            ``prompt_tokens + completion_tokens``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def _total_matches_sum(self) -> TokenUsage:
        expected = self.prompt_tokens + self.completion_tokens
        if self.total_tokens != expected:
            msg = (
                f"TokenUsage.total_tokens={self.total_tokens} does not equal "
                f"prompt_tokens+completion_tokens={expected}"
            )
            raise ValueError(msg)
        return self


class ToolSpec(BaseModel):
    """A tool description the model uses to decide what to call.

    Distinct from :class:`persona.schema.tools.Tool` (the runtime-side
    Protocol) — ``ToolSpec`` is pure data shipped to the provider. Convert
    via :func:`tool_spec_from_tool`.

    Attributes:
        name: Tool name. Must match the persona YAML's ``tools`` allow-list
            and the :class:`persona.schema.tools.ToolCall.name` the model
            emits back.
        description: One-line description the model uses to decide.
        parameters: JSON Schema for the tool's keyword arguments. We do not
            validate well-formedness here — the provider rejects malformed
            schemas with a 400.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCallDelta(BaseModel):
    """Incremental tool-call fragment emitted during streaming.

    Backends concatenate ``arguments_delta`` strings keyed by ``call_id``
    until the stream ends, then emit a final :class:`ToolCall` on the
    consumer side. Spec 02 keeps the deltas raw so callers can reconstruct
    or discard as they prefer.

    Attributes:
        call_id: Provider-supplied identifier; concatenation key.
        name_delta: Incremental fragment of the tool name. Most providers
            send the full name in the first delta; some stream it.
        arguments_delta: Incremental fragment of the arguments JSON string.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    name_delta: str = ""
    arguments_delta: str = ""


class ChatResponse(BaseModel):
    """Single-call response from a :class:`ChatBackend`.

    Attributes:
        content: Assistant text. Empty string is allowed (tool-only
            responses).
        tool_calls: Structured tool requests parsed from the provider's
            native tool-calling response or from the prompt-based shim.
            Empty if the model produced no tool calls.
        usage: Token accounting (always populated).
        model: Echo of the model name the backend used.
        provider: Provider identifier ("anthropic", "openai", "deepseek",
            "groq", "together", "ollama", "local").
        latency_ms: Wall-clock time from request send to response complete,
            measured client-side via :func:`time.perf_counter`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage
    model: str
    provider: str
    latency_ms: float = Field(ge=0.0)


class StreamChunk(BaseModel):
    """One chunk yielded by :meth:`ChatBackend.chat_stream`.

    Attributes:
        delta: Text fragment. Empty string is allowed (e.g., the final
            chunk carrying only usage data).
        tool_call_delta: Tool-call fragment for this chunk, or None.
        is_final: True on the last chunk of the stream. Consumers stop
            iterating after observing this — backends MAY still close their
            iterator after, but ``is_final=True`` is the authoritative end.
        usage: Token accounting; populated only on the final chunk. None on
            intermediate chunks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    delta: str
    tool_call_delta: ToolCallDelta | None = None
    is_final: bool = False
    usage: TokenUsage | None = None


def tool_spec_from_tool(tool: Tool) -> ToolSpec:
    """Convert a runtime :class:`Tool` into a wire-shape :class:`ToolSpec`.

    Spec 01's ``Tool`` Protocol carries ``name``, ``description``, and
    ``parameters_schema``. Spec 02's ``ToolSpec`` is the JSON-shaped
    counterpart shipped to providers. This helper bridges them so callers
    don't redefine the conversion at every site.

    Args:
        tool: Any object satisfying the :class:`Tool` Protocol.

    Returns:
        A :class:`ToolSpec` with the same name, description, and JSON
        schema dict.
    """
    return ToolSpec(
        name=tool.name,
        description=tool.description,
        parameters=dict(tool.parameters_schema),
    )
