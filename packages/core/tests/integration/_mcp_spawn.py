"""Shared helpers for built-in MCP server integration tests (Spec 27 T6-T9).

Spawns a built-in server via its real ``python -m persona.tools.mcp.builtin``
entrypoint on a free loopback port and yields a connected
:class:`~persona.tools.mcp.client.MCPClient`. Reused by the per-server suites so
each one proves its server works end-to-end over real Streamable HTTP, not a mock.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from persona.tools.mcp.client import MCPClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


def free_port() -> int:
    """Grab an ephemeral loopback port (closed before the server re-binds it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_port(port: int, *, timeout_s: float = 20.0) -> None:
    """Block until ``port`` accepts connections or the timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.25)
    raise TimeoutError(f"built-in MCP server never opened port {port}")


@contextlib.contextmanager
def spawn_builtin(name: str, *, extra_env: dict[str, str] | None = None) -> Iterator[str]:
    """Spawn built-in server ``name`` as a subprocess; yield its ``/mcp`` URL."""
    import os

    port = free_port()
    env = {**os.environ, **(extra_env or {})}
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, trusted interpreter
        [
            sys.executable,
            "-m",
            "persona.tools.mcp.builtin",
            name,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        wait_for_port(port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover — defensive
            proc.kill()


@contextlib.asynccontextmanager
async def connected_builtin(
    name: str, *, extra_env: dict[str, str] | None = None
) -> AsyncIterator[MCPClient]:
    """Spawn built-in ``name`` and yield a connected :class:`MCPClient`."""
    with spawn_builtin(name, extra_env=extra_env) as url:
        client = MCPClient(server_name=name, server_url=url)
        await client.connect(strict=True)
        try:
            yield client
        finally:
            await client.disconnect()
