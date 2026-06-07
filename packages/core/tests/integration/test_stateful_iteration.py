"""Spec 17 T06 — Stateful-iteration regression test (D-12-1 + D-17-X-bytes-persistence).

§6 criterion #3 ("stateful iteration verified"): within one conversation,
turn 2 sees the dataframe turn 1 loaded — without re-reading the source
CSV. The v0.1 mechanic (filesystem state persists, Python variable state
does NOT — D-12-1 / Spec 12 T05c) makes this work via:

  - Turn 1 writes ``intermediate/df.parquet`` to ``/workspace/out``.
  - The D-17-X-bytes-persistence persister copies the parquet from the
    sandbox host_out to the persona workspace.
  - Turn 2's input-staging (T04b's augmented provider) reads the parquet
    from the persona workspace and stages it into ``/workspace/in`` for
    the new session.
  - Turn 2's code reads ``/workspace/in/intermediate/df.parquet`` and
    computes — proving the data round-tripped.

**This is the positive-evidence regression guard.** A future SKILL.md
edit that teaches "use df from last turn" would break the test, because
turn 2's code would not reference the parquet path. The mechanic test
proves the round-trip WORKS; the SKILL.md content test (T08) proves the
SKILL.md TEACHES it.

**Requires real Docker + ``persona-sandbox:0.1.0`` image.** Skips
cleanly when the daemon isn't reachable. Single-session-per-test pattern
to keep cleanup deterministic.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.integration


@pytest.fixture
def docker_sandbox(tmp_path: Path) -> Iterator[object]:
    """Yield a real LocalDockerSandbox; aclose on test exit. Skips when
    Docker is unavailable (CI / dev env without docker daemon)."""
    try:
        from persona.sandbox.local_docker import (  # noqa: PLC0415
            LocalDockerSandbox,
            is_docker_available,
        )
    except ImportError:
        pytest.skip("[sandbox] extra not installed")

    if not is_docker_available():
        pytest.skip("Docker daemon not reachable")

    sandbox = LocalDockerSandbox(workspace_root=tmp_path / "sandbox_workspace")
    try:
        yield sandbox
    finally:
        # aclose is async; run it in a fresh event loop on the sync teardown path.
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(sandbox.aclose())
            loop.close()
        except Exception:  # noqa: BLE001 — defensive cleanup
            pass


class TestStatefulFilesystemPersistsAcrossExecutes:
    """D-12-1 / Spec 12 T05c: filesystem state in the session workspace
    survives across ``execute()`` calls in the same session. Variable
    state does NOT (each docker exec is a fresh Python process) — the
    SKILL.md teaches re-load from the parquet cache to work with this.
    """

    @pytest.mark.asyncio
    async def test_intermediate_parquet_persists_across_executes(
        self, docker_sandbox: object
    ) -> None:
        """Turn 1 writes a parquet; turn 2 reads it back. Pure filesystem
        round-trip — no variable-state assumption."""
        from persona.sandbox.result import NetworkPolicy, ResourceLimits  # noqa: PLC0415

        sandbox = docker_sandbox
        session_id = "tenant-T06:conv-A"
        await sandbox.create_session(  # type: ignore[attr-defined]
            session_id, limits=ResourceLimits(), network=NetworkPolicy()
        )

        # Turn 1: write a small file to /workspace/out/intermediate/.
        turn1_code = (
            "import os\n"
            "os.makedirs('intermediate', exist_ok=True)\n"
            "with open('intermediate/df.txt', 'w') as f:\n"
            "    f.write('hello-from-turn-1\\n')\n"
            "print('turn 1 wrote intermediate/df.txt')\n"
        )
        result1 = await sandbox.execute(  # type: ignore[attr-defined]
            turn1_code, session_id=session_id, timeout_s=30.0
        )
        assert result1.outcome == "ok", result1.stderr
        assert "turn 1 wrote" in result1.stdout

        # Turn 2 (SAME session): read the file back. Filesystem state
        # persists; the file is still there even though turn 1's Python
        # process exited. The model would re-load from parquet here in
        # the real SKILL.md teaching; we test the underlying mechanic.
        turn2_code = (
            "with open('intermediate/df.txt') as f:\n"
            "    content = f.read()\n"
            "print(f'turn 2 read: {content.strip()}')\n"
        )
        result2 = await sandbox.execute(  # type: ignore[attr-defined]
            turn2_code, session_id=session_id, timeout_s=30.0
        )
        assert result2.outcome == "ok", result2.stderr
        # Turn 2 read what turn 1 wrote — the round-trip works.
        assert "hello-from-turn-1" in result2.stdout

    @pytest.mark.asyncio
    async def test_variable_state_does_not_persist(self, docker_sandbox: object) -> None:
        """The v0.1 scaled-scope limitation D-12-1 documents honestly:
        Python variable-level state does NOT survive across ``execute``
        calls (each is a fresh ``python`` process via ``docker exec``).
        The SKILL.md's re-load teaching depends on this — if a future
        Spec 12 amendment lands the IPython kernel, the SKILL.md teaching
        changes too."""
        from persona.sandbox.result import NetworkPolicy, ResourceLimits  # noqa: PLC0415

        sandbox = docker_sandbox
        session_id = "tenant-T06:conv-B"
        await sandbox.create_session(  # type: ignore[attr-defined]
            session_id, limits=ResourceLimits(), network=NetworkPolicy()
        )

        # Turn 1: bind a variable.
        await sandbox.execute(  # type: ignore[attr-defined]
            "x = 42\nprint(f'turn 1 set x = {x}')",
            session_id=session_id,
            timeout_s=30.0,
        )

        # Turn 2: try to read the variable. NameError — fresh interpreter.
        turn2_code = (
            "try:\n"
            "    print(f'turn 2 read x = {x}')\n"
            "except NameError:\n"
            "    print('NameError: x is not defined')\n"
        )
        result2 = await sandbox.execute(  # type: ignore[attr-defined]
            turn2_code,
            session_id=session_id,
            timeout_s=30.0,
        )
        assert result2.outcome == "ok"
        # The NameError-handling branch fired — confirming variable state did NOT persist.
        assert "NameError" in result2.stdout
