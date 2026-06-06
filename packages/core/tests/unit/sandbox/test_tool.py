"""Unit tests for ``make_code_execution_tool`` (spec 12 T03).

Covers acceptance §9 criteria #1 (trivial-snippet round-trip), #10
(stdout truncation with explicit marker), #11 (allow-list — verified through
the real Toolbox), and #13 (audit emission).

Tests use the shared :class:`FakeSandbox` (lifted in T03 from test_protocol.py
to ``_fakes.py``) so they exercise the production tool factory against the
Protocol contract without any real container / substrate.
"""

from __future__ import annotations

import hashlib

import pytest
from persona.sandbox import (
    ExecutionResult,
    NetworkPolicy,
    ResourceLimits,
    SandboxError,
    SandboxFile,
    SandboxUnavailableError,
    make_code_execution_tool,
)
from persona.sandbox.tool import TRUNCATION_MARKER_PREFIX
from persona.schema.tools import ToolCall
from persona.tools import MemoryToolAuditLogger, Toolbox

from tests._sandbox_fakes import FakeSandbox

# ---------------------------------------------------------------------------
# §9 #1 — Trivial snippet round-trip + factory wiring
# ---------------------------------------------------------------------------


class TestFactoryShape:
    def test_returns_async_tool_with_canonical_name(self) -> None:
        sandbox = FakeSandbox()
        tool = make_code_execution_tool(sandbox)
        assert tool.name == "code_execution"
        # Description is non-empty so the model can decide to call it.
        assert tool.description

    def test_parameters_schema_only_exposes_code(self) -> None:
        """D-12-4: the model can supply ONLY ``code``. Network / limits /
        session are bound at factory time (composition root, not the model)."""
        sandbox = FakeSandbox()
        tool = make_code_execution_tool(sandbox)
        schema = tool.parameters_schema
        assert "code" in schema["properties"]
        # No network / allowed_hosts / session_id fields — the model cannot
        # widen the policy by passing arguments.
        for forbidden in ("session_id", "network", "allowed_hosts", "limits"):
            assert forbidden not in schema["properties"]

    @pytest.mark.asyncio
    async def test_trivial_snippet_round_trip(self) -> None:
        """Acceptance §9 #1: ``print(2+2)`` returns stdout ``"4\\n"`` /
        ``exit_status=0`` / ``outcome="ok"`` through the tool factory."""
        sandbox = FakeSandbox(
            default_result=ExecutionResult(
                stdout="4\n",
                stderr="",
                exit_status=0,
                outcome="ok",
                duration_ms=1.5,
            )
        )
        tool = make_code_execution_tool(sandbox)
        result = await tool.execute(code="print(2+2)")
        assert result.tool_name == "code_execution"
        assert result.is_error is False
        assert "4\n" in result.content
        assert result.data is not None
        assert result.data["outcome"] == "ok"
        assert result.data["exit_status"] == 0
        assert result.data["duration_ms"] == 1.5

    @pytest.mark.asyncio
    async def test_dispatch_passes_factory_bound_policy(self) -> None:
        """D-12-4: the policy + limits handed to the sandbox come from the
        factory, NOT from the model's tool arguments."""
        sandbox = FakeSandbox()
        policy = NetworkPolicy(enabled=True, allowed_hosts=("example.com",))
        limits = ResourceLimits(memory_mb=128)
        tool = make_code_execution_tool(
            sandbox,
            network_policy=policy,
            resource_limits=limits,
        )
        await tool.execute(code="pass")
        assert sandbox.execute_calls[0]["network"] is policy
        assert sandbox.execute_calls[0]["limits"] is limits

    @pytest.mark.asyncio
    async def test_session_id_provider_resolves_at_dispatch(self) -> None:
        """Kickoff trip-up #6: the provider is called at dispatch time so the
        composition root can read a contextvar set per-request (D-08-1 pattern).
        Tenant-isolation discipline carries: ``{owner_id}:{conversation_id}``."""
        sandbox = FakeSandbox()
        sessions = iter(["user-1:conv-A", "user-1:conv-B", None])
        tool = make_code_execution_tool(
            sandbox,
            session_id_provider=lambda: next(sessions),
        )
        await tool.execute(code="x = 1")
        await tool.execute(code="print(x)")
        await tool.execute(code="print('oneshot')")
        assert sandbox.execute_calls[0]["session_id"] == "user-1:conv-A"
        assert sandbox.execute_calls[1]["session_id"] == "user-1:conv-B"
        assert sandbox.execute_calls[2]["session_id"] is None

    @pytest.mark.asyncio
    async def test_default_session_id_is_none(self) -> None:
        """Default = stateless one-shot when the composition root doesn't
        wire a provider."""
        sandbox = FakeSandbox()
        tool = make_code_execution_tool(sandbox)
        await tool.execute(code="pass")
        assert sandbox.execute_calls[0]["session_id"] is None


# ---------------------------------------------------------------------------
# §9 #10 — Truncation marker is UNAMBIGUOUS and recognisable
# ---------------------------------------------------------------------------


class TestTruncationMarker:
    @pytest.mark.asyncio
    async def test_large_stdout_truncated_with_explicit_marker(self) -> None:
        """The marker prefix is the literal :data:`TRUNCATION_MARKER_PREFIX`
        so downstream consumers (T04 security suite, audit log readers) can
        recognise truncation without ambiguity."""
        # 100 KiB of "A" — well over the default 64 KiB stdout cap.
        big_stdout = "A" * 100_000
        sandbox = FakeSandbox(
            default_result=ExecutionResult(
                stdout=big_stdout,
                stderr="",
                exit_status=0,
                outcome="ok",
                truncated_stdout=True,
            )
        )
        tool = make_code_execution_tool(sandbox)
        result = await tool.execute(code="print('A'*100_000)")
        assert TRUNCATION_MARKER_PREFIX in result.content
        # Truncation flag propagates into the ToolResult shape so the loops'
        # compactor (D-06-4) sees it.
        assert result.truncated is True
        assert result.data is not None
        assert result.data["truncated_stdout"] is True

    @pytest.mark.asyncio
    async def test_small_stdout_no_marker(self) -> None:
        """The marker MUST NOT appear when no truncation occurred —
        downstream consumers rely on the marker's presence/absence to
        discriminate truncated-vs-full output."""
        sandbox = FakeSandbox(
            default_result=ExecutionResult(
                stdout="ok\n",
                stderr="",
                exit_status=0,
                outcome="ok",
            )
        )
        tool = make_code_execution_tool(sandbox)
        result = await tool.execute(code="print('ok')")
        assert TRUNCATION_MARKER_PREFIX not in result.content
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_truncation_marker_is_recognisable(self) -> None:
        """The marker text contains a byte count so a consumer can
        programmatically detect how much was omitted. Pinned shape:
        ``[truncated: N bytes omitted]``."""
        big_stdout = "A" * 100_000
        sandbox = FakeSandbox(
            default_result=ExecutionResult(
                stdout=big_stdout,
                stderr="",
                exit_status=0,
                outcome="ok",
                truncated_stdout=True,
            )
        )
        # Tighten the cap so we know how many bytes get truncated.
        tool = make_code_execution_tool(
            sandbox,
            resource_limits=ResourceLimits(max_stdout_bytes=1024),
        )
        result = await tool.execute(code="...")
        # Marker present, contains the byte-count token, and ends with the
        # closing bracket — three independent assertions a consumer can rely on.
        assert TRUNCATION_MARKER_PREFIX in result.content
        assert "bytes omitted" in result.content
        # Byte count between marker and "bytes omitted" is parseable.
        marker_start = result.content.index(TRUNCATION_MARKER_PREFIX)
        marker_section = result.content[marker_start:]
        assert "]" in marker_section


# ---------------------------------------------------------------------------
# §9 #11 — Allow-list (verified through the real Toolbox)
# ---------------------------------------------------------------------------


class TestAllowList:
    @pytest.mark.asyncio
    async def test_persona_without_code_execution_cannot_invoke(self) -> None:
        """Acceptance §9 #11: the Toolbox's existing allow-list machinery
        (D-03-7) gates ``code_execution``. A persona that doesn't declare it
        triggers :class:`ToolNotAllowedError` — caught by the loops'
        ``_dispatch`` wrappers (spec-11 fix #1)."""
        from persona.errors import ToolNotAllowedError

        sandbox = FakeSandbox()
        tool = make_code_execution_tool(sandbox)
        toolbox = Toolbox([tool], allow_list=["file_read"])  # code_execution NOT declared
        with pytest.raises(ToolNotAllowedError):
            await toolbox.dispatch(
                ToolCall(name="code_execution", args={"code": "print(1)"}, call_id="x")
            )

    @pytest.mark.asyncio
    async def test_persona_with_code_execution_can_invoke(self) -> None:
        sandbox = FakeSandbox(
            default_result=ExecutionResult(
                stdout="1\n",
                stderr="",
                exit_status=0,
                outcome="ok",
            )
        )
        tool = make_code_execution_tool(sandbox)
        toolbox = Toolbox([tool], allow_list=["code_execution"])
        result = await toolbox.dispatch(
            ToolCall(name="code_execution", args={"code": "print(1)"}, call_id="x")
        )
        assert result.is_error is False
        assert "1\n" in result.content


# ---------------------------------------------------------------------------
# §9 #13 — Audit emission
# ---------------------------------------------------------------------------


class TestAuditEmission:
    @pytest.mark.asyncio
    async def test_emits_one_audit_event_per_success(self) -> None:
        """Acceptance §9 #13: every execution emits one audit event."""
        sandbox = FakeSandbox(
            default_result=ExecutionResult(
                stdout="ok\n",
                stderr="",
                exit_status=0,
                outcome="ok",
                duration_ms=10.0,
            )
        )
        logger = MemoryToolAuditLogger()
        tool = make_code_execution_tool(
            sandbox,
            audit_logger=logger,
            persona_id="astrid",
        )
        await tool.execute(code="print('ok')")
        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.action == "execute"
        assert event.persona_id == "astrid"
        assert event.tool_name == "code_execution"
        assert event.resource == "oneshot"
        assert event.is_error is False
        assert event.metadata["outcome"] == "ok"
        assert event.metadata["exit_status"] == "0"

    @pytest.mark.asyncio
    async def test_emits_audit_event_per_failure_with_error_type(self) -> None:
        """D-12-8: audit emits ON FAILURE TOO — the audit trail is the forensic
        record of what was *attempted*, not just what succeeded."""
        sandbox = FakeSandbox(
            side_effect=SandboxUnavailableError(
                "Docker daemon unreachable",
                context={"docker_host": "unix:///var/run/docker.sock"},
            )
        )
        logger = MemoryToolAuditLogger()
        tool = make_code_execution_tool(
            sandbox,
            audit_logger=logger,
            persona_id="astrid",
        )
        result = await tool.execute(code="print(1)")
        assert result.is_error is True
        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.is_error is True
        assert event.metadata["error_type"] == "SandboxUnavailableError"
        assert event.metadata["outcome"] == "error"
        # context keys round-trip into the metadata namespace
        assert event.metadata["ctx_docker_host"] == "unix:///var/run/docker.sock"

    @pytest.mark.asyncio
    async def test_audit_metadata_has_code_sha256_and_preview(self) -> None:
        """D-12-8: inline-with-cap + unconditional sha256."""
        sandbox = FakeSandbox()
        logger = MemoryToolAuditLogger()
        tool = make_code_execution_tool(sandbox, audit_logger=logger)
        code = "x = 1\nprint(x)"
        await tool.execute(code=code)
        event = logger.events[0]
        assert event.metadata["code"] == code
        assert event.metadata["code_truncated"] == "False"
        assert event.metadata["code_sha256"] == hashlib.sha256(code.encode()).hexdigest()

    @pytest.mark.asyncio
    async def test_audit_truncates_large_code_with_marker(self) -> None:
        """D-12-8: code over 4 KiB is truncated inline; the unconditional
        sha256 keeps full-fidelity forensic recovery available."""
        big_code = "# pad\n" * 1500  # ~9 KiB — well over the 4 KiB cap
        sandbox = FakeSandbox()
        logger = MemoryToolAuditLogger()
        tool = make_code_execution_tool(sandbox, audit_logger=logger)
        await tool.execute(code=big_code)
        event = logger.events[0]
        assert event.metadata["code_truncated"] == "True"
        assert TRUNCATION_MARKER_PREFIX in event.metadata["code"]
        # The marker IS recognisable in the audit-log too (user's T03 note)
        assert "bytes omitted" in event.metadata["code"]
        # Full-fidelity sha256 still over the ORIGINAL untruncated code
        assert event.metadata["code_sha256"] == hashlib.sha256(big_code.encode()).hexdigest()

    @pytest.mark.asyncio
    async def test_audit_resource_uses_session_id_when_present(self) -> None:
        """The audit ``resource`` field records the session_id (or "oneshot")
        — so a forensic audit can correlate executions within a session."""
        sandbox = FakeSandbox()
        logger = MemoryToolAuditLogger()
        tool = make_code_execution_tool(
            sandbox,
            audit_logger=logger,
            session_id_provider=lambda: "tenant-1:conv-7",
        )
        await tool.execute(code="pass")
        assert logger.events[0].resource == "tenant-1:conv-7"

    @pytest.mark.asyncio
    async def test_no_audit_logger_silent(self) -> None:
        """When no audit logger is wired (CLI-without-persona case), the
        tool still runs — audit is optional, never required."""
        sandbox = FakeSandbox()
        tool = make_code_execution_tool(sandbox)  # no audit_logger
        result = await tool.execute(code="pass")
        assert result.is_error is False


# ---------------------------------------------------------------------------
# Outcome mapping — non-ok outcomes surface as is_error so the model recovers
# ---------------------------------------------------------------------------


class TestOutcomeMapping:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", ["error", "timeout", "oom", "killed"])
    async def test_non_ok_outcome_is_error(self, outcome: str) -> None:
        sandbox = FakeSandbox(
            default_result=ExecutionResult(
                stdout="",
                stderr="boom",
                exit_status=137,
                outcome=outcome,  # type: ignore[arg-type]
            )
        )
        tool = make_code_execution_tool(sandbox)
        result = await tool.execute(code="...")
        assert result.is_error is True
        assert result.data is not None
        assert result.data["outcome"] == outcome

    @pytest.mark.asyncio
    async def test_produced_files_surface_in_data(self) -> None:
        """Acceptance §9 #2: produced files surface in ``data`` so the runtime
        can persist them to the workspace."""
        files = (
            SandboxFile(path="out/result.csv", size_bytes=42, media_type="text/csv"),
            SandboxFile(path="out/chart.png", size_bytes=1024, media_type="image/png"),
        )
        sandbox = FakeSandbox(
            default_result=ExecutionResult(
                stdout="done\n",
                stderr="",
                exit_status=0,
                outcome="ok",
                produced_files=files,
            )
        )
        tool = make_code_execution_tool(sandbox)
        result = await tool.execute(code="...")
        assert result.data is not None
        assert len(result.data["produced_files"]) == 2
        assert result.data["produced_files"][0]["path"] == "out/result.csv"
        assert result.data["produced_files"][1]["media_type"] == "image/png"
        # Files also surface in human-readable content for the model to read.
        assert "out/result.csv" in result.content
        assert "out/chart.png" in result.content


# ---------------------------------------------------------------------------
# Catch-and-convert (D-12-6 + spec-11 fix #1 discipline)
# ---------------------------------------------------------------------------


class TestSandboxErrorRecovery:
    @pytest.mark.asyncio
    async def test_sandbox_error_converted_to_tool_result(self) -> None:
        """D-12-6: any :class:`SandboxError` subclass is caught at the tool
        factory boundary and converted to ``ToolResult(is_error=True, ...)``.
        Spec-11 fix #1 discipline: the loops' ``_dispatch`` wrappers also
        catch as a second line of defence."""
        sandbox = FakeSandbox(
            side_effect=SandboxUnavailableError(
                "Docker daemon unreachable",
                context={"reason": "daemon_not_running"},
            )
        )
        tool = make_code_execution_tool(sandbox)
        result = await tool.execute(code="print(1)")
        assert result.is_error is True
        assert "SandboxUnavailableError" in result.content
        assert "Docker daemon unreachable" in result.content
        assert result.data is not None
        assert result.data["error_type"] == "SandboxUnavailableError"
        assert result.data["context"]["reason"] == "daemon_not_running"

    @pytest.mark.asyncio
    async def test_non_sandbox_error_propagates_for_decorator_to_catch(self) -> None:
        """Unrelated exceptions are not caught here — the ``@tool`` decorator
        catches them at its boundary (D-03-5) and converts to
        ``ToolResult(is_error=True, ...)``. This split keeps the catch
        boundary clear: sandbox-family inside the tool body; everything else
        at the decorator."""
        sandbox = FakeSandbox(side_effect=RuntimeError("unexpected"))
        tool = make_code_execution_tool(sandbox)
        # The @tool decorator's exception envelope catches; the result reads
        # as "RuntimeError: unexpected".
        result = await tool.execute(code="pass")
        assert result.is_error is True
        assert "RuntimeError" in result.content


# ---------------------------------------------------------------------------
# Ensure the catch-and-convert doesn't double-catch ``SandboxError``
# ---------------------------------------------------------------------------


class TestNarrowCatch:
    @pytest.mark.asyncio
    async def test_base_exception_propagates(self) -> None:
        """``BaseException`` subclasses (``KeyboardInterrupt``, ``SystemExit``)
        propagate — the tool factory's catch is narrow (D-03-5 pattern)."""

        class _Interrupt(BaseException):
            pass

        sandbox = FakeSandbox(side_effect=_Interrupt())
        tool = make_code_execution_tool(sandbox)
        # The @tool decorator also catches Exception (D-03-5), not BaseException,
        # so an Interrupt-class exception propagates all the way out.
        with pytest.raises(_Interrupt):
            await tool.execute(code="...")

    def test_sandbox_error_is_a_persona_error(self) -> None:
        """Cheap sanity check tying T03 back to T01's D-12-6 hierarchy."""
        from persona.errors import PersonaError

        assert issubclass(SandboxError, PersonaError)
