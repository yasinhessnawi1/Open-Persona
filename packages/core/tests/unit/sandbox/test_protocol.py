"""Unit tests for the :class:`CodeSandbox` Protocol (spec 12 T02).

The Protocol is duck-typed via structural subtyping. These tests verify:

- A FakeSandbox satisfies the Protocol via ``isinstance`` (the
  ``@runtime_checkable`` invariant).
- A class missing any of the four required methods (``execute``,
  ``create_session``, ``destroy_session``, ``aclose``) does NOT satisfy it
  — D-12-7 in particular: ``aclose`` is mandatory.
- The FakeSandbox lives at ``conftest`` level (downstream tasks T03/T04
  depend on it) and exhibits the contract every backend must satisfy.
"""

from __future__ import annotations

import pytest
from persona.sandbox import (
    CodeSandbox,
    ExecutionResult,
    NetworkPolicy,
    ResourceLimits,
)

from tests._sandbox_fakes import FakeSandbox


class TestProtocolStructuralSubtyping:
    def test_fake_satisfies_protocol(self) -> None:
        """The reference implementation satisfies ``isinstance`` checks
        (the ``@runtime_checkable`` Protocol invariant T03/T10 depend on)."""
        sandbox = FakeSandbox()
        assert isinstance(sandbox, CodeSandbox)

    def test_class_missing_aclose_does_not_satisfy(self) -> None:
        """D-12-7: ``aclose`` is mandatory. A backend that forgets to ship
        it must NOT satisfy the Protocol — composition-root lifecycle
        registration would otherwise leak substrate resources."""

        class _MissingAclose:
            async def execute(self, code: str, **kwargs: object) -> ExecutionResult:  # noqa: ARG002
                raise NotImplementedError

            async def create_session(
                self,
                session_id: str,  # noqa: ARG002
                *,
                limits: ResourceLimits,  # noqa: ARG002
                network: NetworkPolicy,  # noqa: ARG002
            ) -> None:
                raise NotImplementedError

            async def destroy_session(self, session_id: str) -> None:  # noqa: ARG002
                raise NotImplementedError

        assert not isinstance(_MissingAclose(), CodeSandbox)

    def test_class_missing_execute_does_not_satisfy(self) -> None:
        class _MissingExecute:
            async def create_session(
                self,
                session_id: str,  # noqa: ARG002
                *,
                limits: ResourceLimits,  # noqa: ARG002
                network: NetworkPolicy,  # noqa: ARG002
            ) -> None:
                raise NotImplementedError

            async def destroy_session(self, session_id: str) -> None:  # noqa: ARG002
                raise NotImplementedError

            async def aclose(self) -> None:
                raise NotImplementedError

        assert not isinstance(_MissingExecute(), CodeSandbox)

    def test_class_missing_session_methods_does_not_satisfy(self) -> None:
        class _ExecuteOnly:
            async def execute(self, code: str, **kwargs: object) -> ExecutionResult:  # noqa: ARG002
                raise NotImplementedError

            async def aclose(self) -> None:
                raise NotImplementedError

        assert not isinstance(_ExecuteOnly(), CodeSandbox)


class TestFakeSandboxContract:
    """Pin the FakeSandbox behaviour so T03/T04 can rely on it."""

    @pytest.mark.asyncio
    async def test_execute_captures_call(self) -> None:
        sandbox = FakeSandbox()
        limits = ResourceLimits(memory_mb=256)
        network = NetworkPolicy()
        result = await sandbox.execute(
            "print(1+1)",
            session_id="tenant-a:conv-1",
            timeout_s=5.0,
            limits=limits,
            network=network,
        )
        assert result.outcome == "ok"
        assert len(sandbox.execute_calls) == 1
        captured = sandbox.execute_calls[0]
        assert captured["code"] == "print(1+1)"
        assert captured["session_id"] == "tenant-a:conv-1"
        assert captured["limits"] is limits
        assert captured["network"] is network

    @pytest.mark.asyncio
    async def test_execute_returns_configured_result(self) -> None:
        canned = ExecutionResult(
            stdout="hello\n",
            stderr="",
            exit_status=0,
            outcome="ok",
            duration_ms=42.0,
        )
        sandbox = FakeSandbox(default_result=canned)
        result = await sandbox.execute("print('hello')")
        assert result == canned

    @pytest.mark.asyncio
    async def test_session_lifecycle(self) -> None:
        """Tenant-isolated session_id shape (kickoff trip-up #6) — the
        Protocol takes the value opaque; this test pins the expected
        ``f"{owner_id}:{conversation_id}"`` composition the runtime uses."""
        sandbox = FakeSandbox()
        sid = "user-7:conv-42"
        await sandbox.create_session(
            sid,
            limits=ResourceLimits(),
            network=NetworkPolicy(),
        )
        assert sid in sandbox.created_sessions
        await sandbox.destroy_session(sid)
        assert sid in sandbox.destroyed_sessions

    @pytest.mark.asyncio
    async def test_aclose(self) -> None:
        sandbox = FakeSandbox()
        assert sandbox.aclose_called is False
        await sandbox.aclose()
        assert sandbox.aclose_called is True
