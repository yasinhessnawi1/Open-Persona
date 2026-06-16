"""MCP client wrapper — Streamable HTTP transport (D-03-19).

Connects to an MCP server using the current spec-mandated transport
(``mcp.client.streamable_http``); the legacy HTTP+SSE transport
(``mcp.client.sse``) is NOT used (research §3.3–§3.4).

Lifecycle is managed via :class:`contextlib.AsyncExitStack` so callers
can write ordinary procedural code: ``await client.connect()`` then
``get_tools()`` then eventually ``await client.disconnect()``. This avoids
forcing every caller into nested ``async with`` blocks (the SDK requires
context-manager wrapping for its transports).

Graceful degradation per D-03-20:
- ``connect(strict=True)`` (default) raises :class:`MCPServerUnavailableError`
  on transport failure. Explicit-callers use this.
- ``connect(strict=False)`` logs WARNING + records the failure on the
  injected :class:`ToolAuditLogger` (D-03-21 lifecycle audit), then leaves
  ``get_tools()`` returning ``[]``. ``build_default_toolbox`` (T12) uses
  this default per spec §7.3.

Per-call dispatch audits are skipped (D-03-21). Only connect /
disconnect / server_unavailable lifecycle events emit audit lines.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from persona.errors import MCPServerUnavailableError
from persona.logging import get_logger
from persona.tools.audit import ToolAuditEvent
from persona.tools.mcp.adapter import MCPToolAdapter

if TYPE_CHECKING:
    from collections.abc import Iterable

    from persona.tools.audit import ToolAuditLogger
    from persona.tools.protocol import AsyncTool

__all__ = ["MCPClient", "load_mcp_clients"]

_logger = get_logger("tools.mcp.client")


class MCPClient:
    """Long-lived MCP client over Streamable HTTP.

    Args:
        server_name: Identifier (the key in ``PERSONA_MCP_SERVERS`` config);
            becomes the ``mcp:<server>:`` prefix on discovered tools.
        server_url: HTTP URL of the MCP server endpoint.
        audit_logger: Optional :class:`ToolAuditLogger` for lifecycle events.
        persona_id: Persona identifier for audit records.
        enforce_ssrf: Spec 30 (D-30-4) — when True, connect through the
            SSRF-pinned httpx client (:func:`persona.tools.mcp.ssrf.pinned_httpx_client_factory`)
            so the user-supplied URL is resolve-then-pinned + re-validated on
            every request. Set True for **bring-your-own** servers (untrusted
            user URLs). Left False (default) for built-in / operator-configured
            servers, which bind loopback and are trusted — pinning would block
            them. The guard rides the LIVE connect path either way it's set, not
            just test-connection.
    """

    def __init__(
        self,
        *,
        server_name: str,
        server_url: str,
        audit_logger: ToolAuditLogger | None = None,
        persona_id: str | None = None,
        enforce_ssrf: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._server_name = server_name
        self._server_url = server_url
        self._audit_logger = audit_logger
        self._persona_id = persona_id
        self._enforce_ssrf = enforce_ssrf
        # Spec 30 (D-30-3): auth headers for a bring-your-own server (e.g.
        # ``{"Authorization": "Bearer <token>"}``). Held only in memory for the
        # connection — never logged. None for built-in / operator servers.
        self._headers = headers

        self._exit_stack: AsyncExitStack | None = None
        self._session: object | None = None  # mcp.ClientSession when connected
        self._tools: list[AsyncTool] = []
        self._connected = False

    @property
    def server_name(self) -> str:
        return self._server_name

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, *, strict: bool = True) -> None:
        """Open the transport, initialize the session, discover tools.

        Args:
            strict: If True (default), transport failures raise
                :class:`MCPServerUnavailableError`. If False, failures log a
                warning and leave ``get_tools()`` returning ``[]``.

        Raises:
            MCPServerUnavailableError: when ``strict=True`` and the
                server cannot be reached.
        """
        # Import locally so unit tests can patch `mcp.client.streamable_http`
        # without forcing every import of this module to materialise the SDK.
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as e:
            self._emit_audit(action="server_unavailable", error=type(e).__name__)
            if strict:
                msg = "mcp SDK not installed — pip install mcp"
                raise MCPServerUnavailableError(
                    msg,
                    context={
                        "server": self._server_name,
                        "url": self._server_url,
                        "error": type(e).__name__,
                    },
                ) from e
            _logger.warning(
                "mcp SDK not installed; omitting server tools",
                server=self._server_name,
            )
            return

        stack = AsyncExitStack()
        try:
            # Spec 30 (D-30-4): bring-your-own servers connect through the
            # SSRF-pinned httpx client so the untrusted user URL is
            # resolve-then-pinned + re-validated on every request (incl. redirects)
            # — on the LIVE path, not just test-connection. Built-in / operator
            # servers (loopback, trusted) use the SDK default factory.
            # Build the connect kwargs conditionally so the trusted built-in /
            # operator path keeps the exact ``streamablehttp_client(url)`` call
            # shape (no headers, no factory) it had pre-spec-30.
            connect_kwargs: dict[str, object] = {}
            if self._headers is not None:
                connect_kwargs["headers"] = self._headers
            if self._enforce_ssrf:
                from persona.tools.mcp.ssrf import pinned_httpx_client_factory

                connect_kwargs["httpx_client_factory"] = pinned_httpx_client_factory
            transport_ctx = streamablehttp_client(self._server_url, **connect_kwargs)  # type: ignore[arg-type]
            read, write, _get_session_id = await stack.enter_async_context(transport_ctx)
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools_result = await session.list_tools()
        except Exception as e:  # noqa: BLE001 — wrap into domain exception
            await stack.aclose()
            self._emit_audit(action="server_unavailable", error=type(e).__name__)
            if strict:
                _logger.warning(
                    "mcp connect failed (strict)",
                    server=self._server_name,
                    url=self._server_url,
                    error=type(e).__name__,
                )
                msg = f"cannot reach MCP server {self._server_name}"
                raise MCPServerUnavailableError(
                    msg,
                    context={
                        "server": self._server_name,
                        "url": self._server_url,
                        "error": type(e).__name__,
                    },
                ) from e
            _logger.warning(
                "mcp server unavailable; omitting from toolbox",
                server=self._server_name,
                url=self._server_url,
                error=type(e).__name__,
            )
            return

        self._exit_stack = stack
        self._session = session
        self._tools = [
            MCPToolAdapter(
                server_name=self._server_name,
                session=session,
                tool_def=t,
            )
            for t in tools_result.tools
        ]
        self._connected = True
        _logger.info(
            "mcp connected",
            server=self._server_name,
            url=self._server_url,
            tool_count=len(self._tools),
        )
        self._emit_audit(action="connect")

    def get_tools(self) -> list[AsyncTool]:
        """Return adapter-wrapped tools discovered at connect time.

        Empty list if the client is not connected (e.g., ``strict=False``
        connect that failed). Callers MUST NOT mutate the returned list.
        """
        return list(self._tools)

    async def disconnect(self, *, reason: str = "user_close") -> None:
        """Close the MCP session and underlying transport.

        Safe to call multiple times; second call is a no-op.
        """
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as e:  # noqa: BLE001 — disconnect must not raise
                _logger.warning(
                    "mcp disconnect raised",
                    server=self._server_name,
                    error=type(e).__name__,
                )
            self._exit_stack = None
        self._session = None
        self._tools = []
        was_connected = self._connected
        self._connected = False
        if was_connected:
            _logger.info("mcp disconnected", server=self._server_name, reason=reason)
            self._emit_audit(action="disconnect", reason=reason)

    def _emit_audit(
        self,
        *,
        action: str,
        reason: str | None = None,
        error: str | None = None,
    ) -> None:
        if self._audit_logger is None:
            return
        metadata: dict[str, str] = {
            "url": self._server_url,
            "transport": "streamable_http",
        }
        if reason is not None:
            metadata["reason"] = reason
        if error is not None:
            metadata["error"] = error
        # ToolAuditAction is a Literal — cast through the runtime check.
        # Valid values: write, connect, disconnect, server_unavailable.
        from typing import cast

        from persona.tools.audit import ToolAuditAction

        self._audit_logger.emit(
            ToolAuditEvent(
                timestamp=datetime.now(UTC),
                persona_id=self._persona_id,
                tool_name=f"mcp:{self._server_name}",
                action=cast("ToolAuditAction", action),
                resource=self._server_name,
                is_error=action == "server_unavailable",
                metadata=metadata,
            )
        )


async def load_mcp_clients(
    servers: dict[str, str],
    *,
    audit_logger: ToolAuditLogger | None = None,
    persona_id: str | None = None,
    strict: bool = False,
) -> list[MCPClient]:
    """Connect to every server in the ``servers`` dict.

    Used by ``build_default_toolbox`` (T12) to wire MCP servers from
    ``PersonaCoreConfig.mcp_servers``. Per D-03-20, ``strict=False`` is
    the default — unreachable servers are logged + audit-trailed and
    their tools are omitted.

    Args:
        servers: ``{server_name: server_url}`` mapping (the parsed
            ``PERSONA_MCP_SERVERS`` env var; see D-03-22).
        audit_logger: Optional :class:`ToolAuditLogger` for lifecycle events.
        persona_id: Persona identifier for audit records.
        strict: If True, the first unreachable server raises
            :class:`MCPServerUnavailableError`. Default False.

    Returns:
        One :class:`MCPClient` per entry. Disconnected clients are still
        returned so the caller can ``await client.disconnect()`` on each.
    """
    clients: list[MCPClient] = []
    for server_name, url in servers.items():
        client = MCPClient(
            server_name=server_name,
            server_url=url,
            audit_logger=audit_logger,
            persona_id=persona_id,
        )
        await client.connect(strict=strict)
        clients.append(client)
    return clients


def _collect_tools(clients: Iterable[MCPClient]) -> list[AsyncTool]:
    """Flatten the tools across a set of MCP clients."""
    result: list[AsyncTool] = []
    for c in clients:
        result.extend(c.get_tools())
    return result
