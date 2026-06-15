"""Shared harness for built-in MCP servers (Spec 27 T5, D-27-2).

Every built-in server is a thin FastMCP app exposed over the spec-mandated
Streamable HTTP transport (D-03-19) and bound to loopback only (D-27-12). This
module owns the two pieces every server shares:

- :data:`ServerBuilder` — the ``(host, port) -> FastMCP`` builder signature each
  server module implements (it constructs ``FastMCP(name, host=host, port=port)``
  and registers its ``@server.tool()`` functions);
- :func:`run_streamable_http` — runs a built FastMCP server over Streamable HTTP;
- :func:`serve` — the dispatch the ``python -m persona.tools.mcp.builtin``
  entrypoint calls: look up a builder in the registry, build it, run it.

The registry (:data:`SERVER_BUILDERS`) lives in the package ``__init__`` so each
server module registers itself there; this module stays import-light (FastMCP is
only needed for typing here) so the launcher can import it cheaply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias

from persona.errors import MCPBuiltinServerError

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.fastmcp import FastMCP

    #: A server factory: given a bind host + port, build (not run) the server.
    ServerBuilder: TypeAlias = Callable[[str, int], FastMCP]

__all__ = [
    "DEFAULT_BIND_HOST",
    "STREAMABLE_HTTP_TRANSPORT",
    "run_streamable_http",
    "serve",
]

#: The only transport built-ins speak (D-03-19; legacy SSE / stdio excluded).
STREAMABLE_HTTP_TRANSPORT: Literal["streamable-http"] = "streamable-http"

#: Built-ins bind loopback ONLY — never reachable off-box (D-27-12).
DEFAULT_BIND_HOST = "127.0.0.1"


def run_streamable_http(server: FastMCP) -> None:
    """Run a built FastMCP server over Streamable HTTP (blocks until stopped).

    Host + port are baked into the server at construction; this only selects the
    transport. Separated from the builder so tests can build a server without
    starting its event loop.
    """
    server.run(transport=STREAMABLE_HTTP_TRANSPORT)


def serve(name: str, *, host: str = DEFAULT_BIND_HOST, port: int) -> None:
    """Build and run the built-in server ``name`` (the ``-m`` entrypoint target).

    Args:
        name: A registered built-in server name (a key of ``SERVER_BUILDERS``).
        host: Bind address; defaults to loopback (D-27-12). The launcher always
            passes ``127.0.0.1``.
        port: TCP port to bind.

    Raises:
        MCPBuiltinServerError: ``name`` is not a registered built-in server.
    """
    # Imported here (not at module top) to avoid an import cycle: the package
    # __init__ imports the server modules, which import this harness.
    from persona.tools.mcp.builtin import SERVER_BUILDERS

    builder = SERVER_BUILDERS.get(name)
    if builder is None:
        msg = f"unknown built-in MCP server: {name!r}"
        raise MCPBuiltinServerError(
            msg,
            context={"name": name, "available": ", ".join(sorted(SERVER_BUILDERS))},
        )
    server = builder(host, port)
    run_streamable_http(server)
