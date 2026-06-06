"""The ``code_execution`` first-class tool factory (spec 12 T03).

Wraps any :class:`CodeSandbox` into an :class:`AsyncTool` registered in the
existing :class:`Toolbox`. The persona's tool allow-list machinery (D-03-7)
gates whether a given persona can call ``code_execution`` at all; the spec-11
fix-#1 ``_dispatch`` wrappers in both loops catch ``ToolNotAllowedError`` and
:class:`SandboxError` and convert to ``ToolResult(is_error=True, ...)`` so the
SSE stream never crashes.

§9 acceptance touched by T03:
- **#1** trivial snippet round-trip — the tool's wire shape.
- **#10** large stdout truncated at ``max_stdout_bytes`` with an EXPLICIT
  marker, never dropped silently. The marker is the literal prefix
  ``[truncated: N bytes omitted]`` so a downstream consumer can recognise it
  without ambiguity. Asserted by T04's "truncated-code is recognisably
  truncated" test row.
- **#11** allow-list — the Toolbox enforces the literal-only allow-list
  unchanged (D-03-7); this factory just produces an :class:`AsyncTool` named
  ``code_execution``.
- **#13** audit emission — every execution emits exactly one
  :class:`ToolAuditEvent` with ``action="execute"`` (D-12-8); ``metadata``
  carries ``code`` (truncated to 4 KiB with the same marker), ``code_sha256``
  (full-fidelity reference), ``outcome``, ``duration_ms``, ``exit_status``.

Decisions exercised:
- **D-12-4**: ``NetworkPolicy`` is constructed by the factory FROM the persona,
  not passed by the model in the tool call. The model only supplies ``code``.
- **D-12-1**: ``session_id`` is provided by an injected ``session_id_provider``
  callable that the composition root (T10) sets per-conversation (tenant-
  isolated as ``f"{owner_id}:{conversation_id}"``; kickoff trip-up #6).
  Default returns ``None`` → stateless one-shot.
- **D-12-6**: ``SandboxError`` family is caught and converted to
  ``ToolResult(is_error=True, ...)`` — the loops' ``_dispatch`` wrappers also
  catch as a second line of defence (spec-11 fix #1 discipline).
- **D-12-8**: Audit emits even on failure (the audit trail is the forensic
  record of what was attempted, not just what succeeded). Failed-write
  pattern (D-03-21) is the contrast — we audit *all* executions.
- **D-12-14**: :class:`ExecutionResult` is Pydantic-frozen; tool factory reads
  fields via attribute access.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona.sandbox.errors import SandboxError
from persona.sandbox.result import (
    ExecutionResult,
    NetworkPolicy,
    ResourceLimits,
)
from persona.schema.tools import ToolResult
from persona.tools.audit import ToolAuditEvent
from persona.tools.protocol import AsyncTool, tool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from persona.sandbox.protocol import CodeSandbox
    from persona.tools.audit import ToolAuditLogger

__all__ = ["TRUNCATION_MARKER_PREFIX", "make_code_execution_tool"]

_logger = get_logger("sandbox.tool")

# The truncation marker is a *literal prefix* so downstream consumers can
# recognise truncated stdout / audit-code without ambiguity (T04 asserts the
# marker is present and parseable). Includes the byte count so the consumer
# knows how much was omitted.
TRUNCATION_MARKER_PREFIX = "[truncated:"
"""Literal prefix used by every truncation marker the tool factory writes.

Full marker shape: ``"\\n\\n[truncated: N bytes omitted]"`` where ``N`` is
the byte count omitted. Downstream consumers (the audit log, the model
itself reading stdout) can detect truncation by searching for this prefix.
"""

# D-12-8 audit-code cap: 4 KiB covers the median LLM-generated snippet
# (~500 chars per R-12-4) with headroom. Over the cap, code is truncated in
# the audit log but the unconditional code_sha256 keeps full-fidelity recovery.
_AUDIT_CODE_CAP_BYTES = 4 * 1024

# Default factory description fed to the model. Kept terse — the model's
# system prompt is the right place for additional usage guidance.
_DEFAULT_DESCRIPTION = (
    "Execute Python code in a secure sandbox. Use for calculations, data analysis, "
    "file processing, generating charts, and producing documents. Returns stdout, "
    "stderr, and any files the code writes to the workspace. Network access is "
    "disabled by default."
)


def make_code_execution_tool(
    sandbox: CodeSandbox,
    *,
    network_policy: NetworkPolicy | None = None,
    resource_limits: ResourceLimits | None = None,
    audit_logger: ToolAuditLogger | None = None,
    persona_id: str | None = None,
    session_id_provider: Callable[[], str | None] | None = None,
    pre_execute_hook: Callable[[], Awaitable[None]] | None = None,
    on_execute_success: Callable[[ExecutionResult], Awaitable[None]] | None = None,
    description: str = _DEFAULT_DESCRIPTION,
) -> AsyncTool:
    """Build the ``code_execution`` :class:`AsyncTool`.

    Args:
        sandbox: The :class:`CodeSandbox` implementation to dispatch to —
            :class:`LocalDockerSandbox` (T05) for the CLI / open-source path;
            ``HostedSandbox`` (T08) for the hosted path.
        network_policy: Per-persona network policy (D-12-4 — constructed by
            the caller from the persona's YAML, never passed by the model).
            ``None`` defaults to ``NetworkPolicy()`` (egress disabled).
        resource_limits: Per-persona resource caps. ``None`` defaults to
            :class:`ResourceLimits` (sensible conservative defaults from
            spec §4.1).
        audit_logger: Optional :class:`ToolAuditLogger`. When provided, every
            execution emits one :class:`ToolAuditEvent` with
            ``action="execute"`` (D-12-8 / acceptance §9 #13).
        persona_id: Persona identifier for audit records. ``None`` for CLI
            development; audit lines then route to ``_cli.tools.jsonl``.
        session_id_provider: Callable returning the current conversation's
            session_id (tenant-isolated as ``f"{owner_id}:{conversation_id}"``
            per kickoff trip-up #6). ``None`` ⇒ stateless one-shot execution.
            Defaults to ``lambda: None`` if not supplied. Resolved lazily on
            every dispatch — supports composition roots that use contextvars
            for per-request session state (D-08-1 pattern).
        pre_execute_hook: Async hook awaited BEFORE the substrate call. The api
            wires this to ``pool.acquire(...)`` so the per-tenant sandbox session
            is lazy-eager-acquired on first dispatch (D-12-17: warm=0;
            substrate cold-start paid here, not at conversation creation).
            Hook may raise :class:`SandboxError` — caught by the same path as
            substrate failures and surfaced to the model as a structured
            ``ToolResult(is_error=True)``. ``None`` ⇒ no prelude (CLI / tests).
        on_execute_success: Async hook fired AFTER a successful execute
            (``result.outcome == "ok"``) and BEFORE the tool's :class:`ToolResult`
            is returned. The api wires this to the **flat per-execution credits
            deduction** per **D-12-3** (mirrors D-08-6: only successful executions
            are billed; OOM/timeout/killed are not). Hook exceptions are caught
            and logged so a credits-write failure cannot break the tool's result.
            ``None`` ⇒ no hook (CLI path; tests that don't exercise billing).
        description: Tool description fed to the model. The default covers
            the spec §6 wording; override to surface persona-specific guidance.

    Returns:
        An :class:`AsyncTool` named ``code_execution`` registered against the
        provided sandbox. The model supplies only ``code: str`` — every other
        parameter (session, policy, limits) is bound at factory time.

    Notes:
        The returned tool follows the @tool / Toolbox contract unchanged:
        argument-validation errors and body-raised exceptions are converted
        to ``ToolResult(is_error=True, ...)`` by the ``@tool`` decorator
        (D-03-5). :class:`SandboxError` subclasses (D-12-6 family) are caught
        here at the body and surfaced as structured failure results so the
        model can recover (kickoff trip-up: ``SandboxUnavailableError`` =
        "Docker not available" → model explains to user). The conversation /
        agentic loops' ``_dispatch`` wrappers catch any escape as a second
        line of defence (spec-11 fix #1).
    """
    network = network_policy if network_policy is not None else NetworkPolicy()
    limits = resource_limits if resource_limits is not None else ResourceLimits()
    _provider = session_id_provider if session_id_provider is not None else (lambda: None)

    @tool(name="code_execution", description=description)
    async def code_execution(code: str) -> ToolResult:
        session_id = _provider()
        code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        try:
            if pre_execute_hook is not None:
                await pre_execute_hook()
            result = await sandbox.execute(
                code,
                session_id=session_id,
                timeout_s=limits.wall_clock_s,
                limits=limits,
                network=network,
            )
        except SandboxError as exc:
            # D-12-6 catch-and-convert: any sandbox-family error surfaces as
            # a structured failure result so the model can recover. The
            # loops' _dispatch wrappers catch unrelated exceptions as a
            # second line of defence (spec-11 fix #1).
            _logger.warning(
                "code_execution dispatch failed",
                exc_type=type(exc).__name__,
                persona_id=persona_id or "<unknown>",
            )
            _emit_audit_for_error(
                audit_logger=audit_logger,
                persona_id=persona_id,
                session_id=session_id,
                code=code,
                code_sha256=code_sha256,
                exc=exc,
            )
            return ToolResult(
                tool_name="code_execution",
                content=f"{type(exc).__name__}: {exc}",
                is_error=True,
                data={"error_type": type(exc).__name__, "context": exc.context},
                metadata={
                    "outcome": "error",
                    "session_id": session_id or "",
                    "code_sha256": code_sha256,
                },
            )

        # D-12-3 credits hook — fires on outcome=="ok" only (mirrors D-08-6
        # "failed turn deducts nothing"). Hook failure is logged and swallowed
        # so a billing-write error cannot break the tool's structured result.
        if on_execute_success is not None and result.outcome == "ok":
            try:
                await on_execute_success(result)
            except Exception as exc:  # noqa: BLE001 — hook failure must not break the tool
                _logger.warning(
                    "code_execution credits hook failed; tool result unchanged",
                    exc_type=type(exc).__name__,
                    persona_id=persona_id or "<unknown>",
                    session_id=session_id or "",
                )

        formatted = _format_result_for_model(result, limits)
        _emit_audit_for_result(
            audit_logger=audit_logger,
            persona_id=persona_id,
            session_id=session_id,
            code=code,
            code_sha256=code_sha256,
            result=result,
        )
        return ToolResult(
            tool_name="code_execution",
            content=formatted,
            # outcome != "ok" surfaces as is_error so the model recovers (the
            # loops also feed is_error back without crashing the stream).
            is_error=result.outcome != "ok",
            data={
                "outcome": result.outcome,
                "exit_status": result.exit_status,
                "duration_ms": result.duration_ms,
                "produced_files": [
                    {"path": f.path, "size_bytes": f.size_bytes, "media_type": f.media_type}
                    for f in result.produced_files
                ],
                "truncated_stdout": result.truncated_stdout,
                "truncated_files": result.truncated_files,
            },
            truncated=result.truncated_stdout or result.truncated_files,
            metadata={
                "outcome": result.outcome,
                "session_id": session_id or "",
                "duration_ms": f"{result.duration_ms:.1f}",
                "exit_status": str(result.exit_status),
                "code_sha256": code_sha256,
            },
        )

    return code_execution


# ----- internals ---------------------------------------------------------------


def _format_result_for_model(result: ExecutionResult, limits: ResourceLimits) -> str:
    """Render an :class:`ExecutionResult` for the model to read.

    Layout:
      1. ``stdout`` — truncated at ``limits.max_stdout_bytes`` with the
         explicit marker if exceeded (acceptance §9 #10).
      2. A trailing outcome line so the model can see immediately what
         happened (``outcome=ok exit=0 duration_ms=12.3``).
      3. If non-empty, ``stderr`` under a ``-- stderr --`` divider.
      4. If any, a ``-- files --`` list of produced files (one per line:
         ``path (size_bytes, media_type)``).

    Truncation marker is the literal prefix :data:`TRUNCATION_MARKER_PREFIX`
    so downstream consumers (T04 security suite, the audit log) can detect
    truncation unambiguously.
    """
    stdout, stdout_truncated = _truncate(result.stdout, limits.max_stdout_bytes)
    sections: list[str] = []
    if stdout:
        sections.append(stdout)
    elif not stdout_truncated:
        # Make absence of stdout visible to the model when nothing else
        # would land in the rendered output (e.g., outcome=ok exit=0
        # produced one file but printed nothing).
        sections.append("(no stdout)")

    sections.append(
        f"-- outcome --\noutcome={result.outcome} "
        f"exit_status={result.exit_status} "
        f"duration_ms={result.duration_ms:.1f}"
    )

    if result.stderr:
        stderr, _ = _truncate(result.stderr, limits.max_stdout_bytes)
        sections.append(f"-- stderr --\n{stderr}")

    if result.produced_files:
        rendered_files = "\n".join(
            f"{f.path} ({f.size_bytes} bytes, {f.media_type})" for f in result.produced_files
        )
        suffix = "\n[truncated: produced file list capped]" if result.truncated_files else ""
        sections.append(f"-- files --\n{rendered_files}{suffix}")

    return "\n\n".join(sections)


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate ``text`` to at most ``max_bytes`` UTF-8 bytes with an explicit marker.

    Returns ``(truncated_text, was_truncated)``. The marker is appended to the
    returned text when truncation fires; the marker itself adds bytes but the
    overall byte count of the returned string is allowed to slightly exceed
    ``max_bytes`` so the cap is on *content*, not on the rendered length —
    keeps the rendering predictable and the marker unambiguous.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    omitted = len(encoded) - max_bytes
    # Decode the kept prefix, replacing any partial trailing multi-byte char
    # to avoid raising on a mid-character cut.
    kept = encoded[:max_bytes].decode("utf-8", errors="replace")
    marker = f"\n\n{TRUNCATION_MARKER_PREFIX} {omitted} bytes omitted]"
    return kept + marker, True


def _truncate_for_audit(code: str) -> tuple[str, bool]:
    """Truncate code for audit-log inline storage (D-12-8: 4 KiB cap).

    The unconditional ``code_sha256`` companion field keeps full-fidelity
    forensic recovery — this is the inline preview, not the full record.
    """
    return _truncate(code, _AUDIT_CODE_CAP_BYTES)


def _emit_audit_for_result(
    *,
    audit_logger: ToolAuditLogger | None,
    persona_id: str | None,
    session_id: str | None,
    code: str,
    code_sha256: str,
    result: ExecutionResult,
) -> None:
    """Emit one ``action="execute"`` audit event for a completed execution."""
    if audit_logger is None:
        return
    code_preview, code_truncated = _truncate_for_audit(code)
    audit_logger.emit(
        ToolAuditEvent(
            timestamp=datetime.now(UTC),
            persona_id=persona_id,
            tool_name="code_execution",
            action="execute",
            resource=session_id or "oneshot",
            is_error=result.outcome != "ok",
            metadata={
                "code": code_preview,
                "code_truncated": str(code_truncated),
                "code_sha256": code_sha256,
                "outcome": result.outcome,
                "exit_status": str(result.exit_status),
                "duration_ms": f"{result.duration_ms:.1f}",
                "truncated_stdout": str(result.truncated_stdout),
                "truncated_files": str(result.truncated_files),
            },
        )
    )


def _emit_audit_for_error(
    *,
    audit_logger: ToolAuditLogger | None,
    persona_id: str | None,
    session_id: str | None,
    code: str,
    code_sha256: str,
    exc: SandboxError,
) -> None:
    """Emit one ``action="execute"`` audit event for a backend-raised failure.

    Distinct from :func:`_emit_audit_for_result` because there's no
    :class:`ExecutionResult` to read — the failure mode lives in the
    exception type and its ``context``.
    """
    if audit_logger is None:
        return
    code_preview, code_truncated = _truncate_for_audit(code)
    audit_logger.emit(
        ToolAuditEvent(
            timestamp=datetime.now(UTC),
            persona_id=persona_id,
            tool_name="code_execution",
            action="execute",
            resource=session_id or "oneshot",
            is_error=True,
            metadata={
                "code": code_preview,
                "code_truncated": str(code_truncated),
                "code_sha256": code_sha256,
                "outcome": "error",
                "error_type": type(exc).__name__,
                **{f"ctx_{k}": v for k, v in exc.context.items()},
            },
        )
    )
