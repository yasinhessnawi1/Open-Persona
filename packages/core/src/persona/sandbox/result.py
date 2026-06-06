"""Boundary-crossing types for the code-execution sandbox (spec 12 T01).

Per **D-12-14**, these are Pydantic v2 frozen models with ``extra="forbid"`` —
NOT the spec §4.1 ``@dataclass(frozen=True, slots=True)`` sketches. Phase-1
convention re-applied (D-01-12 / D-02-2 / D-03-3 / D-04-1 / D-05-9 / D-06-1).

The types cross four boundaries:

1. Returned from the ``code_execution`` tool as ``ToolResult.data`` (T03).
2. Serialized into the conversation via ``format_tool_result``.
3. Audited via the ``ToolAuditLogger`` port (D-12-8).
4. (For the hosted path) cross the SSE / API surface.

Container collection fields are :class:`tuple` rather than :class:`list` — a
list inside a frozen model can still mutate its contents; tuples cannot.

D-12-4 reminder: ``NetworkPolicy`` is constructed by the tool factory from
the persona's YAML, NOT passed by the model in the tool call. The substrate
egress filter (R-12-5) still blocks metadata + RFC-1918 ranges regardless
of ``allowed_hosts``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ExecutionOutcome",
    "ExecutionResult",
    "NetworkPolicy",
    "ResourceLimits",
    "SandboxFile",
]


ExecutionOutcome = Literal["ok", "error", "timeout", "oom", "killed"]
"""Discriminator for :class:`ExecutionResult`.

- ``"ok"`` — code returned exit status 0.
- ``"error"`` — code raised / returned non-zero within limits.
- ``"timeout"`` — wall-clock cap hit; substrate killed the process.
- ``"oom"`` — memory cap hit; substrate's OOM killer fired.
- ``"killed"`` — any other forced kill (pids-limit, cancel, host shutdown).
"""


class ResourceLimits(BaseModel):
    """Per-execution resource caps enforced by the substrate, not by the code.

    Limits live **one layer below** the model-generated code so the code itself
    cannot raise them. Spec §4.2 "Limits are explicit and enforced by the
    substrate". Defaults are conservative.

    Attributes:
        cpu_cores: CPU allocation. Fractional values supported by both Docker
            (``--cpus``) and E2B (``cpu_count``).
        memory_mb: Memory cap in MiB. Substrate OOM-killer fires above.
        wall_clock_s: Hard timeout in seconds. Substrate kills above.
        disk_mb: Workspace disk quota in MiB.
        max_stdout_bytes: Cap on stdout returned to the conversation. Beyond
            this, output is truncated with an explicit marker — never dropped
            silently (spec-02/04/06 fail-safe-truncation pattern).
        max_produced_files: Cap on the count of produced files reported back.
        max_produced_file_mb: Per-file size cap on produced files.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cpu_cores: float = Field(default=1.0, gt=0.0)
    memory_mb: int = Field(default=512, gt=0)
    wall_clock_s: float = Field(default=30.0, gt=0.0)
    disk_mb: int = Field(default=256, gt=0)
    max_stdout_bytes: int = Field(default=64_000, gt=0)
    max_produced_files: int = Field(default=20, ge=0)
    max_produced_file_mb: int = Field(default=50, gt=0)


class NetworkPolicy(BaseModel):
    """Per-sandbox network egress policy.

    Network is **OFF by default** (the safe default per spec §4.2). When
    enabled, only hosts in ``allowed_hosts`` may be reached — and substrate
    egress rules (R-12-5: ``169.254.0.0/16``, RFC-1918, IPv6 link-local, etc.)
    still block those ranges **regardless** of the allow-list.

    Constructed by the tool factory from the persona's YAML (D-12-4) — never
    passed by the model in the tool call.

    Attributes:
        enabled: Whether egress is permitted at all.
        allowed_hosts: Hosts to allow when ``enabled=True``. The substrate
            egress filter still blocks metadata + private ranges first.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    allowed_hosts: tuple[str, ...] = Field(default_factory=tuple)


class SandboxFile(BaseModel):
    """A file referenced by or produced by the sandbox.

    Used in two directions:

    - **Into the sandbox:** ``content_bytes`` is the payload to write at
      ``path`` inside the workspace input area.
    - **Out of the sandbox:** ``content_bytes`` may be populated (small files
      inlined) or ``None`` (large files referenced only — caller reads from
      the workspace by ``path``).

    Attributes:
        path: Path relative to the workspace root. Never absolute.
        content_bytes: File payload, or ``None`` when only referenced.
        size_bytes: Size in bytes.
        media_type: MIME-style media type; default ``application/octet-stream``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    content_bytes: bytes | None = None
    size_bytes: int = Field(default=0, ge=0)
    media_type: str = "application/octet-stream"


class ExecutionResult(BaseModel):
    """The result of one :meth:`CodeSandbox.execute` call.

    Fed back into the conversation as a :class:`ToolResult` by the
    ``code_execution`` tool factory (T03). The model reads ``stdout`` /
    ``stderr`` / ``outcome``; the runtime persists ``produced_files`` to the
    workspace; the audit logger records ``duration_ms`` + ``outcome`` (D-12-8).

    Attributes:
        stdout: Captured standard output. May be truncated (see
            ``truncated_stdout``); never dropped silently.
        stderr: Captured standard error. ANSI codes stripped; absolute
            container paths sanitized by the backend before this point.
        exit_status: Process exit status. 0 on success; non-zero on error;
            -1 when the substrate killed the process before it could exit.
        outcome: Discriminator across ``ok`` / ``error`` / ``timeout`` /
            ``oom`` / ``killed``.
        produced_files: Files the code wrote to the workspace output area.
        duration_ms: Wall-clock duration in milliseconds.
        truncated_stdout: ``True`` when stdout exceeded ``max_stdout_bytes``
            and was truncated with an explicit marker.
        truncated_files: ``True`` when produced files exceeded the count or
            size caps in ``ResourceLimits``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stdout: str
    stderr: str
    exit_status: int
    outcome: ExecutionOutcome
    produced_files: tuple[SandboxFile, ...] = Field(default_factory=tuple)
    duration_ms: float = Field(default=0.0, ge=0.0)
    truncated_stdout: bool = False
    truncated_files: bool = False
