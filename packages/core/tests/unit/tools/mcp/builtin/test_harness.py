"""Unit tests for the built-in MCP server harness (Spec 27 T5)."""

from __future__ import annotations

from typing import Any

import pytest
from persona.errors import MCPBuiltinServerError
from persona.tools.mcp import builtin
from persona.tools.mcp.builtin import _harness
from persona.tools.mcp.builtin._harness import (
    DEFAULT_BIND_HOST,
    STREAMABLE_HTTP_TRANSPORT,
    run_streamable_http,
    serve,
)


class _FakeServer:
    """Stand-in FastMCP: records the transport it was run with."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.ran_transport: str | None = None

    def run(self, *, transport: str) -> None:
        self.ran_transport = transport


def test_default_bind_host_is_loopback() -> None:
    # D-27-12: built-ins bind loopback only.
    assert DEFAULT_BIND_HOST == "127.0.0.1"


def test_streamable_http_is_the_only_transport() -> None:
    # D-03-19: streamable-http only.
    assert STREAMABLE_HTTP_TRANSPORT == "streamable-http"


def test_run_streamable_http_selects_the_transport() -> None:
    server = _FakeServer("127.0.0.1", 9000)
    run_streamable_http(server)  # type: ignore[arg-type]
    assert server.ran_transport == "streamable-http"


def test_serve_builds_then_runs_a_registered_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: dict[str, Any] = {}

    def _builder(host: str, port: int) -> _FakeServer:
        server = _FakeServer(host, port)
        built["server"] = server
        return server

    monkeypatch.setitem(builtin.SERVER_BUILDERS, "fake", _builder)  # type: ignore[arg-type]
    serve("fake", host="127.0.0.1", port=9123)

    server = built["server"]
    assert (server.host, server.port) == ("127.0.0.1", 9123)
    assert server.ran_transport == "streamable-http"


def test_serve_defaults_host_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _builder(host: str, port: int) -> _FakeServer:
        captured["host"] = host
        return _FakeServer(host, port)

    monkeypatch.setitem(builtin.SERVER_BUILDERS, "fake", _builder)  # type: ignore[arg-type]
    serve("fake", port=9124)
    assert captured["host"] == "127.0.0.1"


def test_serve_unknown_server_raises() -> None:
    with pytest.raises(MCPBuiltinServerError, match="unknown built-in MCP server"):
        serve("does-not-exist", port=9125)


def test_main_parses_args_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    from persona.tools.mcp.builtin import __main__ as entry

    calls: dict[str, Any] = {}

    def _fake_serve(name: str, *, host: str, port: int) -> None:
        calls.update(name=name, host=host, port=port)

    monkeypatch.setattr(entry, "serve", _fake_serve)
    entry.main(["time", "--host", "127.0.0.1", "--port", "8123"])
    assert calls == {"name": "time", "host": "127.0.0.1", "port": 8123}


def test_main_requires_port() -> None:
    from persona.tools.mcp.builtin import __main__ as entry

    # argparse exits (SystemExit) on the missing required --port before serve runs.
    with pytest.raises(SystemExit):
        entry.main(["time"])  # no --port


def test_serve_imports_without_a_cycle() -> None:
    # The harness imports SERVER_BUILDERS lazily; importing it must not blow up.
    assert isinstance(builtin.SERVER_BUILDERS, dict)
    assert _harness.serve is serve
