"""``persona.sandbox`` — code-execution sandbox surface (spec 12).

T01 ships the boundary-crossing types + error hierarchy. Subsequent tasks add:

- T02: the :class:`CodeSandbox` Protocol.
- T03: the ``code_execution`` first-class tool factory.
- T05a/b/c: :class:`LocalDockerSandbox` (under a ``[sandbox]`` extra in
  ``pyproject.toml`` once T05a lands — pulls the Docker SDK).
- T06: the pinned sandbox image (Dockerfile + ``requirements.txt``).
- T07: the substrate egress filter (DOCKER-USER iptables chain).

The runtime and the agentic loop import only the Protocol and the boundary
types from this module; concrete backends are wired by the composition
root, exactly like every other tool factory (D-04-10 use_skill precedent).

``HostedSandbox`` (T08) lives in ``persona-api`` per the spec-07 core/api
split precedent (D-07-3): protocol + lightweight backend in core; the
heavyweight production backend in api.
"""

from __future__ import annotations

from persona.sandbox.errors import (
    CodeSandboxError,
    ExecutionTimeoutError,
    ResourceLimitError,
    SandboxError,
    SandboxQuotaExceededError,
    SandboxUnavailableError,
)
from persona.sandbox.protocol import CodeSandbox
from persona.sandbox.result import (
    ExecutionOutcome,
    ExecutionResult,
    NetworkPolicy,
    ResourceLimits,
    SandboxFile,
)
from persona.sandbox.tool import TRUNCATION_MARKER_PREFIX, make_code_execution_tool

__all__ = [
    "TRUNCATION_MARKER_PREFIX",
    "CodeSandbox",
    "CodeSandboxError",
    "ExecutionOutcome",
    "ExecutionResult",
    "ExecutionTimeoutError",
    "NetworkPolicy",
    "ResourceLimitError",
    "ResourceLimits",
    "SandboxError",
    "SandboxFile",
    "SandboxQuotaExceededError",
    "SandboxUnavailableError",
    "make_code_execution_tool",
]
