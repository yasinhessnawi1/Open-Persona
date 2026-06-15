"""Built-in ``filesystem`` MCP server (Spec 27 T8, D-27-2 / D-27-12).

A thin FastMCP Streamable-HTTP server that exposes sandboxed file read/write over
MCP. It is the ONE built-in with write capability, so it is contained in depth:

1. **Loopback bind** — the launcher binds ``127.0.0.1`` only (D-27-12), so the
   server is never reachable off-box.
2. **Path-traversal guard** — read/write delegate to the in-tree
   ``file_read`` / ``file_write`` tools, whose
   :func:`persona.tools._sandbox.resolve_sandbox_path` rejects ``..``, absolute
   paths, NULL bytes, and symlinks escaping the sandbox, and which open with
   ``O_NOFOLLOW``. The guard is single-sourced — this server adds no new file I/O.
3. **Non-root user** — the launcher runs the subprocess as the non-root persona
   user (D-27-12), so even a guard bypass cannot act as the API user.

The sandbox root is ``PersonaCoreConfig.tools_sandbox_root`` (env
``PERSONA_TOOLS_SANDBOX_ROOT``), inherited from the API process at spawn — the
same root the in-process ``file_read`` / ``file_write`` tools use.

Exposed as ``mcp:filesystem:read_file`` + ``mcp:filesystem:write_file``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.config import PersonaCoreConfig
from persona.tools.builtin.file_read import make_file_read_tool
from persona.tools.builtin.file_write import make_file_write_tool

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

__all__ = ["build"]


def build(host: str, port: int) -> FastMCP:
    """Build (do not run) the ``filesystem`` FastMCP server bound to ``host:port``.

    Reads the sandbox root from the environment-derived config and ensures it
    exists so the first read/write does not race on a missing directory.
    """
    from mcp.server.fastmcp import FastMCP

    sandbox_root = PersonaCoreConfig().tools_sandbox_root
    # Create the root up front so reads/writes resolve deterministically; the
    # path guard still rejects anything that escapes it.
    sandbox_root.mkdir(parents=True, exist_ok=True)

    read_tool = make_file_read_tool(sandbox_root=sandbox_root)
    write_tool = make_file_write_tool(sandbox_root=sandbox_root)

    server = FastMCP("filesystem", host=host, port=port)

    @server.tool()
    async def read_file(path: str) -> str:
        """Read a UTF-8 text file from the persona's sandboxed workspace. Use a
        relative path like 'out/report.md'. Absolute paths and '..' escapes are
        rejected.
        """
        result = await read_tool.execute(path=path)
        if result.is_error:
            raise ValueError(result.content)
        return result.content

    @server.tool()
    async def write_file(path: str, content: str) -> str:
        """Write a UTF-8 text file into the persona's sandboxed workspace,
        creating parent directories. Use a relative path like 'out/report.md'.
        Absolute paths and '..' escapes are rejected.
        """
        result = await write_tool.execute(path=path, content=content)
        if result.is_error:
            raise ValueError(result.content)
        return result.content

    return server
