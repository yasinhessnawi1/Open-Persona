"""Spec 25 §2.4 (operator-pass 2026-06-13) — E2B idle-reap → recoverable no_session.

The reported failure: a stateful sandbox is reaped server-side by E2B (idle
timeout); the next ``run_code`` raises a ``TimeoutException`` ("The sandbox was
not found", code 502). Before the fix, ``_run_and_marshal`` marshalled that into
a plain ``outcome="error"`` result, so the tool wrapper's auto-recovery (T09 /
D-25-4 — which keys on a RAISED ``reason="no_session"`` error) never fired, the
dead handle stayed in ``_sessions``, and the model retried the SAME dead
sandboxId forever.

After the fix: the stateful execute path detects the reap, evicts the dead
handle, and raises ``CodeSandboxError(reason="no_session", cause="substrate_
reaped")`` — which T09 then auto-recovers (recreate + retry once).
"""

from __future__ import annotations

import pytest
from persona.sandbox.errors import CodeSandboxError
from persona_api.sandbox.hosted import HostedSandbox, _is_sandbox_reaped


class _TimeoutException(Exception):  # noqa: N818 — mirrors E2B SDK's real class name
    """Stand-in for the E2B SDK's TimeoutException (only the message matters)."""


class _ReapedSession:
    """A session whose run_code raises as if E2B reaped it server-side."""

    files = None  # input_files loop is skipped (no input files in these tests)

    def run_code(self, _code: str, *, timeout: float) -> object:  # noqa: ARG002
        raise _TimeoutException(
            '{"sandboxId":"iya15fdzg4fjlbo94amvn","message":"The sandbox was not found","code":502}'
        )


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ('TimeoutException: {"message":"The sandbox was not found","code":502}', True),
        ("TimeoutException: sandbox was not found", True),
        ("SomeError: upstream returned 502 Bad Gateway", True),
        ("ValueError: file not found: data.csv", False),  # user code error, no "sandbox"
        ("NameError: name 'x' is not defined", False),
        ("", False),
    ],
)
def test_is_sandbox_reaped_signature(stderr: str, expected: bool) -> None:
    assert _is_sandbox_reaped(stderr) is expected


@pytest.mark.asyncio
async def test_reaped_session_raises_no_session_and_evicts() -> None:
    sandbox = HostedSandbox()
    sandbox._sessions["alice:c1"] = _ReapedSession()  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(CodeSandboxError) as ei:
        await sandbox.execute("import matplotlib", session_id="alice:c1", timeout_s=5.0)

    # Re-surfaced as the recoverable reason the tool wrapper (T09) handles.
    assert ei.value.context["reason"] == "no_session"
    assert ei.value.context["cause"] == "substrate_reaped"
    assert ei.value.context["session_id"] == "alice:c1"
    # Dead handle evicted so a recreate (T09) does not reuse it.
    assert "alice:c1" not in sandbox._sessions  # noqa: SLF001


@pytest.mark.asyncio
async def test_genuine_code_error_is_not_treated_as_reap() -> None:
    """A normal user-code error must still marshal to an error result, not raise."""

    class _ErroringSession:
        files = None

        def run_code(self, _code: str, *, timeout: float) -> object:  # noqa: ARG002
            raise RuntimeError("boom: something not found in user code")  # no 'sandbox'/502

    sandbox = HostedSandbox()
    sandbox._sessions["bob:c2"] = _ErroringSession()  # type: ignore[assignment]  # noqa: SLF001

    result = await sandbox.execute("oops()", session_id="bob:c2", timeout_s=5.0)
    assert result.outcome == "error"
    assert "boom" in result.stderr
    # Session NOT evicted — it's a code error, not a reap.
    assert "bob:c2" in sandbox._sessions  # noqa: SLF001
