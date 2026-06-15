"""End-to-end MCP infrastructure test (Spec 27 T1 — the Phase-1 gate, formalized).

Spec 04 shipped the MCP client + adapter; Spec 27's Phase-1 verification proved
the full path works against a *real* Streamable-HTTP MCP server. This test makes
that verification a permanent regression guard: a genuine FastMCP server
(separate process, real ``mcp`` SDK, real HTTP) is driven through the in-tree
:class:`~persona.tools.mcp.client.MCPClient` →
:class:`~persona.tools.mcp.adapter.MCPToolAdapter` → :class:`ToolResult` path.

It exercises every §2.1 leg: discovery, adapter wrapping (``mcp:<server>:<tool>``
naming + JSON-Schema), dispatch, result return, and connect/disconnect lifecycle
audit. If any leg regresses (the Spec 15 §2.9 "defined-but-unwired" failure
mode), this test fails — unlike the unit tests, which mock the session.

Marked ``integration`` (spawns a subprocess + binds a loopback port), so the
default ``pytest`` run skips it; run with ``pytest -m integration -k mcp_e2e``.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest
from persona.tools.audit import ToolAuditEvent, ToolAuditLogger
from persona.tools.mcp.client import MCPClient

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# A minimal but real FastMCP Streamable-HTTP server. Authored inline so this
# test depends on nothing but the `mcp` SDK (NOT the Spec-27 builtin harness,
# which is verified separately by its own tests).
_SERVER_SRC = """
import sys
from mcp.server.fastmcp import FastMCP

port = int(sys.argv[1])
mcp = FastMCP("e2e", host="127.0.0.1", port=port)


@mcp.tool()
def echo(text: str) -> str:
    "Echo the input back, prefixed, to prove dispatch + result return."
    return f"echoed:{text}"


mcp.run(transport="streamable-http")
"""


def _free_port() -> int:
    """Grab an ephemeral loopback port (closed before the server re-binds it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, *, timeout_s: float = 20.0) -> None:
    """Block until ``port`` accepts connections or the timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.25)
    raise TimeoutError(f"MCP test server never opened port {port}")


@pytest.fixture
def mcp_server_url() -> Iterator[str]:
    """Spawn a real FastMCP Streamable-HTTP server; yield its ``/mcp`` URL."""
    port = _free_port()
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, trusted interpreter
        [sys.executable, "-c", _SERVER_SRC, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover — defensive
            proc.kill()


class _CapturingAudit(ToolAuditLogger):
    """Records every emitted audit event for lifecycle assertions."""

    def __init__(self) -> None:
        self.events: list[ToolAuditEvent] = []

    def emit(self, event: ToolAuditEvent) -> None:
        self.events.append(event)


async def test_mcp_client_discovers_adapts_dispatches_against_real_server(
    mcp_server_url: str,
) -> None:
    """The full discover → adapt → dispatch → result → audit path works live."""
    audit = _CapturingAudit()
    client = MCPClient(
        server_name="e2e",
        server_url=mcp_server_url,
        audit_logger=audit,
        persona_id="e2e_persona",
    )

    await client.connect(strict=True)
    assert client.is_connected

    # Discovery + adapter wrapping (mcp:<server>:<tool> naming + JSON-Schema).
    tools = client.get_tools()
    by_name = {t.name: t for t in tools}
    assert "mcp:e2e:echo" in by_name
    echo = by_name["mcp:e2e:echo"]
    assert echo.parameters_schema.get("properties", {}).get("text", {}).get("type") == "string"

    # Dispatch + result return.
    result = await echo.execute(text="hello")
    assert not result.is_error
    assert "echoed:hello" in result.content
    assert result.tool_name == "mcp:e2e:echo"

    await client.disconnect()
    assert not client.is_connected

    # Lifecycle audit (connect + disconnect emitted; D-03-21).
    actions = [e.action for e in audit.events]
    assert "connect" in actions
    assert "disconnect" in actions


async def test_mcp_tool_call_after_disconnect_degrades_gracefully(
    mcp_server_url: str,
) -> None:
    """A dispatch after the session closes returns an error ToolResult, never raises."""
    client = MCPClient(server_name="e2e", server_url=mcp_server_url)
    await client.connect(strict=True)
    echo = next(t for t in client.get_tools() if t.name == "mcp:e2e:echo")
    await client.disconnect()

    # The adapter holds a now-dead session; the call must not raise.
    result = await echo.execute(text="after-close")
    assert result.is_error
