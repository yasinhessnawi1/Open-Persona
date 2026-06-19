"""Hosted-sandbox writable out-dir parity (turn-resilience Task 2).

The model is told (by the prompt builder + every document-generation skill) to
write produced files under ``/workspace/out``. The local Docker substrate
creates + mounts it read-write and runs with it as the working dir; the E2B
substrate boots with ``/home/user`` and has NO ``/workspace/out`` — so model
code writing to ``/workspace/out/<file>`` raised ``FileNotFoundError`` on the
hosted path (which amplified the empty-assistant-message bug by thrashing the
turn).

These tests assert the hosted sandbox ENSURES the documented out-dir:
  1. ``_create_sandbox`` calls the SDK ``files.make_dir('/workspace/out')``.
  2. ``_run_and_marshal`` prepends a belt-and-braces ``os.makedirs`` bootstrap
     so the dir exists even when the SDK lacks ``make_dir`` / the dir was reaped.
  3. The produced-file READ path resolves relative refs under ``/workspace/out``.

The E2B client is faked exactly as the existing hosted tests fake it (a stand-in
object exposing only the methods the code under test touches).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from persona_api.sandbox.hosted import (
    _HOSTED_WORKSPACE_OUT,
    _WORKSPACE_OUT_BOOTSTRAP,
    HostedSandbox,
)


class _FakeFiles:
    """Stand-in for ``sandbox.files`` recording make_dir / read calls."""

    def __init__(self, *, has_make_dir: bool = True, read_payload: bytes = b"") -> None:
        self.made_dirs: list[str] = []
        self.read_paths: list[str] = []
        self._read_payload = read_payload
        if has_make_dir:
            self.make_dir = self._make_dir  # type: ignore[method-assign]

    def _make_dir(self, path: str) -> None:
        self.made_dirs.append(path)

    def read(self, path: str, *, format: str) -> bytes:  # noqa: A002, ARG002 — mirrors SDK sig
        self.read_paths.append(path)
        return self._read_payload


class _FakeExecution:
    """Minimal E2B ``Execution`` stand-in (no error, empty logs)."""

    class _Logs:
        stdout: list[str] = []
        stderr: list[str] = []

    def __init__(self) -> None:
        self.logs = self._Logs()
        self.error = None


class _FakeSandbox:
    """Stand-in for the E2B ``Sandbox`` exposing ``files`` + ``run_code``."""

    def __init__(self, *, has_make_dir: bool = True) -> None:
        self.files = _FakeFiles(has_make_dir=has_make_dir)
        self.run_code_calls: list[str] = []

    def run_code(self, code: str, *, timeout: float) -> _FakeExecution:  # noqa: ARG002
        self.run_code_calls.append(code)
        return _FakeExecution()

    def kill(self) -> None: ...


def test_create_sandbox_ensures_workspace_out() -> None:
    fake = _FakeSandbox()
    sandbox = HostedSandbox()
    # Patch the lazy SDK import so _create_sandbox builds our fake.
    with patch("e2b_code_interpreter.Sandbox", return_value=fake, create=True):
        from persona.sandbox.result import NetworkPolicy, ResourceLimits

        created = sandbox._create_sandbox(  # noqa: SLF001
            limits=ResourceLimits(), network=NetworkPolicy()
        )
    assert created is fake
    assert _HOSTED_WORKSPACE_OUT in fake.files.made_dirs


def test_create_sandbox_survives_sdk_without_make_dir() -> None:
    # An SDK build lacking files.make_dir must not break sandbox creation —
    # the per-execute bootstrap is the fallback.
    fake = _FakeSandbox(has_make_dir=False)
    sandbox = HostedSandbox()
    with patch("e2b_code_interpreter.Sandbox", return_value=fake, create=True):
        from persona.sandbox.result import NetworkPolicy, ResourceLimits

        created = sandbox._create_sandbox(  # noqa: SLF001
            limits=ResourceLimits(), network=NetworkPolicy()
        )
    assert created is fake  # no exception; no make_dir attribute present


def test_run_and_marshal_prepends_out_dir_bootstrap() -> None:
    fake = _FakeSandbox()
    sandbox = HostedSandbox()
    result = sandbox._run_and_marshal(  # noqa: SLF001
        fake,  # type: ignore[arg-type]
        "print('hi')",
        timeout_s=5.0,
        input_files=[],
    )
    assert result.outcome == "ok"
    assert len(fake.run_code_calls) == 1
    executed = fake.run_code_calls[0]
    assert executed.startswith(_WORKSPACE_OUT_BOOTSTRAP)
    assert "print('hi')" in executed


@pytest.mark.asyncio
async def test_read_produced_file_resolves_under_workspace_out() -> None:
    fake = _FakeSandbox()
    fake.files = _FakeFiles(read_payload=b"PNGDATA")  # type: ignore[assignment]
    sandbox = HostedSandbox()
    sandbox._sessions["alice:c1"] = fake  # type: ignore[assignment]  # noqa: SLF001

    data = await sandbox.read_produced_file_bytes("alice:c1", "chart.png")

    assert data == b"PNGDATA"
    assert fake.files.read_paths == [f"{_HOSTED_WORKSPACE_OUT}/chart.png"]


@pytest.mark.asyncio
async def test_read_produced_file_honours_absolute_ref() -> None:
    fake = _FakeSandbox()
    fake.files = _FakeFiles(read_payload=b"X")  # type: ignore[assignment]
    sandbox = HostedSandbox()
    sandbox._sessions["alice:c1"] = fake  # type: ignore[assignment]  # noqa: SLF001

    await sandbox.read_produced_file_bytes("alice:c1", "/workspace/out/sub/x.csv")

    assert fake.files.read_paths == ["/workspace/out/sub/x.csv"]
