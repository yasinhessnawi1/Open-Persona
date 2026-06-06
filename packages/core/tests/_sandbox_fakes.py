"""Shared test fakes for sandbox unit tests (spec 12 T02 → T03 → T04).

``_FakeSandbox`` is a :class:`CodeSandbox`-Protocol-conforming reference
implementation used by:

- ``test_protocol.py`` (T02) — pins the structural contract.
- ``test_tool.py`` (T03) — verifies ``make_code_execution_tool`` round-trips
  against a Protocol-conforming backend.
- ``test_security_suite.py`` (T04) — the adversarial security suite
  parametrises sandbox factories, with this fake as the "no-op control"
  baseline against which T05's real ``LocalDockerSandbox`` runs the real
  adversarial tests.

The fake captures every call so tests can assert what the tool factory /
toolbox / loops dispatched, and returns a configurable
:class:`ExecutionResult` so the consumer-side mapping can be exercised.
"""

from __future__ import annotations

from persona.sandbox import (
    ExecutionResult,
    NetworkPolicy,
    ResourceLimits,
    SandboxFile,
)

__all__ = ["FakeSandbox"]


class FakeSandbox:
    """Reference Protocol implementation for sandbox unit tests.

    Returns ``default_result`` on every :meth:`execute` call (or
    ``ExecutionResult(stdout="", stderr="", exit_status=0, outcome="ok")`` if
    none supplied). Tracks invocations so tests can assert dispatch occurred
    with the expected arguments.

    Use ``side_effect`` to raise a :class:`SandboxError` subclass instead of
    returning a result — for testing the T03 catch-and-convert.
    """

    def __init__(
        self,
        *,
        default_result: ExecutionResult | None = None,
        side_effect: BaseException | None = None,
    ) -> None:
        self._default_result = default_result or ExecutionResult(
            stdout="",
            stderr="",
            exit_status=0,
            outcome="ok",
        )
        self._side_effect = side_effect
        self.execute_calls: list[dict[str, object]] = []
        self.created_sessions: set[str] = set()
        self.destroyed_sessions: set[str] = set()
        self.aclose_called = False

    async def execute(
        self,
        code: str,
        *,
        language: str = "python",
        session_id: str | None = None,
        timeout_s: float = 30.0,
        limits: ResourceLimits | None = None,
        network: NetworkPolicy | None = None,
        input_files: list[SandboxFile] | None = None,
    ) -> ExecutionResult:
        self.execute_calls.append(
            {
                "code": code,
                "language": language,
                "session_id": session_id,
                "timeout_s": timeout_s,
                "limits": limits,
                "network": network,
                "input_files": input_files,
            }
        )
        if self._side_effect is not None:
            raise self._side_effect
        return self._default_result

    async def create_session(
        self,
        session_id: str,
        *,
        limits: ResourceLimits,  # noqa: ARG002 — Protocol contract; fake doesn't use it
        network: NetworkPolicy,  # noqa: ARG002 — Protocol contract; fake doesn't use it
    ) -> None:
        self.created_sessions.add(session_id)

    async def destroy_session(self, session_id: str) -> None:
        self.destroyed_sessions.add(session_id)

    async def aclose(self) -> None:
        self.aclose_called = True
