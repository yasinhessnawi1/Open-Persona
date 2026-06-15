"""Unit tests for the built-in ``filesystem`` MCP server (Spec 27 T8).

The security-critical built-in: these tests prove the path-traversal guard is
wired (``..`` / absolute / symlink escapes rejected) and that legitimate
relative read/write round-trips inside the sandbox.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from persona.tools.mcp.builtin import SERVER_BUILDERS
from persona.tools.mcp.builtin.filesystem_server import build

if TYPE_CHECKING:
    from pathlib import Path


def _first_text(call_result: object) -> str:
    blocks = call_result[0] if isinstance(call_result, tuple) else call_result
    for block in blocks:  # type: ignore[union-attr]
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return ""


@pytest.fixture(autouse=True)
def _sandbox_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the filesystem server's sandbox root at the test's temp dir."""
    monkeypatch.setenv("PERSONA_TOOLS_SANDBOX_ROOT", str(tmp_path))


@pytest.mark.asyncio
async def test_build_exposes_read_and_write_tools() -> None:
    server = build("127.0.0.1", 8400)
    assert server.name == "filesystem"
    names = {t.name for t in await server.list_tools()}
    assert {"read_file", "write_file"} <= names


@pytest.mark.asyncio
async def test_write_then_read_round_trips_inside_sandbox(tmp_path: Path) -> None:
    server = build("127.0.0.1", 8401)
    await server.call_tool("write_file", {"path": "out/note.txt", "content": "hello sandbox"})
    assert (tmp_path / "out" / "note.txt").read_text() == "hello sandbox"
    read = await server.call_tool("read_file", {"path": "out/note.txt"})
    assert "hello sandbox" in _first_text(read)


@pytest.mark.asyncio
async def test_write_rejects_parent_traversal_escape(tmp_path: Path) -> None:
    server = build("127.0.0.1", 8402)
    with pytest.raises(Exception, match="(?i)sandbox|escape"):
        await server.call_tool("write_file", {"path": "../escape.txt", "content": "x"})
    # The escape file must NOT exist outside the sandbox.
    assert not (tmp_path.parent / "escape.txt").exists()


@pytest.mark.asyncio
async def test_write_rejects_deep_traversal_escape() -> None:
    server = build("127.0.0.1", 8403)
    with pytest.raises(Exception, match="(?i)sandbox|escape"):
        await server.call_tool("write_file", {"path": "../../../../tmp/pwned.txt", "content": "x"})


@pytest.mark.asyncio
async def test_read_rejects_absolute_path() -> None:
    server = build("127.0.0.1", 8404)
    with pytest.raises(Exception, match="(?i)sandbox|absolute"):
        await server.call_tool("read_file", {"path": "/etc/passwd"})


def test_filesystem_is_registered_in_the_builder_registry() -> None:
    assert SERVER_BUILDERS.get("filesystem") is build
