"""Built-in MCP servers (Spec 27, D-27-1 / D-27-2).

Persona authors a small set of MCP servers as thin FastMCP apps served over
Streamable HTTP and bound to loopback only. Each server module exposes a
``build(host, port) -> FastMCP`` factory; this package assembles them into
:data:`SERVER_BUILDERS`, the registry the ``python -m persona.tools.mcp.builtin``
entrypoint (:mod:`persona.tools.mcp.builtin.__main__`) and the API launcher
dispatch against by name.

The launch set (catalog ``kind="builtin"``): ``time``, ``calculator``,
``filesystem`` (default-enabled) + ``weather`` (opt-in). External servers
(fetch / github) ship NO code and are absent here — they are configured via
``PERSONA_MCP_SERVERS`` (D-27-11).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.tools.mcp.builtin._harness import (
    DEFAULT_BIND_HOST,
    STREAMABLE_HTTP_TRANSPORT,
    run_streamable_http,
    serve,
)
from persona.tools.mcp.builtin.calculator_server import build as _build_calculator
from persona.tools.mcp.builtin.filesystem_server import build as _build_filesystem
from persona.tools.mcp.builtin.time_server import build as _build_time
from persona.tools.mcp.builtin.weather_server import build as _build_weather

if TYPE_CHECKING:
    from persona.tools.mcp.builtin._harness import ServerBuilder

__all__ = [
    "DEFAULT_BIND_HOST",
    "SERVER_BUILDERS",
    "STREAMABLE_HTTP_TRANSPORT",
    "run_streamable_http",
    "serve",
]

#: Registry of built-in server name → ``(host, port) -> FastMCP`` builder.
#: Keys MUST match the catalog's authored (``kind="builtin"``) names.
SERVER_BUILDERS: dict[str, ServerBuilder] = {
    "time": _build_time,
    "calculator": _build_calculator,
    "filesystem": _build_filesystem,
    "weather": _build_weather,
}
