"""``build_default_toolbox`` — compose a Toolbox from config + Persona (T12).

Wires the four built-in tools and (asynchronously) loads any MCP servers
declared in :class:`PersonaCoreConfig.mcp_servers`. The persona's
``tools`` allow-list filters which tools the Toolbox advertises.

Graceful degradation: MCP servers are connected with ``strict=False``
per D-03-20 — unreachable servers log a warning and audit a
``server_unavailable`` event, but the toolbox still builds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona.tools.builtin.calculator import make_calculator_tool
from persona.tools.builtin.currency_convert import make_currency_convert_tool
from persona.tools.builtin.datetime import make_datetime_tool
from persona.tools.builtin.file_read import make_file_read_tool
from persona.tools.builtin.file_write import make_file_write_tool
from persona.tools.builtin.json_query import make_json_query_tool
from persona.tools.builtin.regex_match import make_regex_match_tool
from persona.tools.builtin.text_diff import make_text_diff_tool
from persona.tools.builtin.web_fetch import make_web_fetch_tool
from persona.tools.builtin.web_search import make_web_search_tool
from persona.tools.mcp.client import load_mcp_clients
from persona.tools.toolbox import Toolbox

if TYPE_CHECKING:
    from persona.config import PersonaCoreConfig
    from persona.schema.persona import Persona
    from persona.tools.audit import ToolAuditLogger
    from persona.tools.mcp.client import MCPClient
    from persona.tools.protocol import AsyncTool
    from persona.tools.workspace_persister import WorkspacePersister

__all__ = ["build_default_toolbox"]

_logger = get_logger("tools.factory")


async def build_default_toolbox(
    config: PersonaCoreConfig,
    persona: Persona,
    *,
    tool_audit_logger: ToolAuditLogger | None = None,
    extra_tools: list[AsyncTool] | None = None,
    workspace_persister: WorkspacePersister | None = None,
    extra_mcp_servers: dict[str, str] | None = None,
    extra_mcp_clients: list[MCPClient] | None = None,
) -> tuple[Toolbox, list[MCPClient]]:
    """Compose a Toolbox for the given persona.

    Args:
        config: Runtime configuration with `web_search_*`, `tools_sandbox_root`,
            and `mcp_servers` fields populated from env vars (spec-03 D-03-9
            through D-03-23).
        persona: The persona whose `tools` allow-list filters which tools
            the Toolbox advertises. Empty allow-list means the Toolbox
            advertises nothing (still safe to dispatch through; every call
            raises `ToolNotAllowedError`).
        tool_audit_logger: Optional logger for `file_write` + MCP lifecycle
            events (D-03-21).
        extra_tools: Additional tools the composition root supplies — notably the
            `use_skill` tool (D-04-10: NOT auto-registered; the runtime/API
            composes it when the persona has skills). Folded into the Toolbox
            alongside the built-ins + MCP tools, subject to the same allow-list.
        workspace_persister: Optional `WorkspacePersister` injected into
            `file_write` so written files are mirrored to the persona workspace
            and surfaced as `ToolResult.artifacts` (inline file cards). `None`
            (CLI / tests) ⇒ `file_write` produces its pre-persister result shape.
        extra_mcp_servers: Additional ``{name: url}`` MCP servers to connect
            beyond those in ``config.mcp_servers`` (Spec 27 D-27-3). The API
            launcher passes the lazily-spawned built-in MCP server URLs here;
            entries override same-named ``config.mcp_servers`` entries. ``None``
            (CLI / test path) ⇒ only the env-configured servers are connected.
        extra_mcp_clients: Spec 30 (D-30-4/6) — pre-built bring-your-own MCP
            clients (constructed with ``enforce_ssrf=True`` + any auth headers by
            the API factory, which holds the decryption key). They are connected
            here (``strict=False``, graceful) and their tools are added to the
            toolbox AND auto-allowed: the persona↔server *assignment* is the
            authorization (D-30-6), so BYO tool names are admitted regardless of
            the YAML ``tools`` allow-list (which never names them).

    Returns:
        A tuple ``(toolbox, mcp_clients)``. The caller is responsible for
        eventually calling ``await client.disconnect()`` on each MCP client
        (typically during shutdown). The clients are returned even when
        their connect failed (graceful degradation) so the caller can
        still disconnect any that succeeded.
    """
    # Built-in tools (always present; the persona's allow-list decides
    # whether they're exposed via get_specs / dispatch).
    api_key = (
        config.web_search_api_key.get_secret_value()
        if config.web_search_api_key is not None
        else None
    )
    builtins: list[AsyncTool] = [
        make_web_search_tool(
            provider_name=config.web_search_provider,
            api_key=api_key,
        ),
        make_web_fetch_tool(),
        make_file_read_tool(sandbox_root=config.tools_sandbox_root),
        make_file_write_tool(
            sandbox_root=config.tools_sandbox_root,
            audit_logger=tool_audit_logger,
            persona_id=persona.persona_id,
            persister=workspace_persister,
        ),
        # Spec 26 — general-utility built-ins (deny-by-default; the persona's
        # allow-list still gates whether each is advertised).
        make_calculator_tool(),
        make_datetime_tool(),
        make_regex_match_tool(),
        make_json_query_tool(),
        make_text_diff_tool(),
        make_currency_convert_tool(
            provider_name=config.currency_provider,
            api_key=(
                config.currency_api_key.get_secret_value()
                if config.currency_api_key is not None
                else None
            ),
        ),
    ]

    # MCP-discovered tools. Graceful degradation per D-03-20. Built-in MCP
    # servers (Spec 27) arrive via ``extra_mcp_servers`` and override same-named
    # env-configured entries.
    mcp_clients: list[MCPClient] = []
    mcp_tools: list[AsyncTool] = []
    parsed_servers = {**config.mcp_servers_parsed, **(extra_mcp_servers or {})}
    if parsed_servers:
        mcp_clients = await load_mcp_clients(
            parsed_servers,
            audit_logger=tool_audit_logger,
            persona_id=persona.persona_id,
            strict=False,
        )
        for c in mcp_clients:
            mcp_tools.extend(c.get_tools())

    # Spec 30 (D-30-4/6) — bring-your-own MCP clients (SSRF-pinned, pre-built by
    # the API factory). Connect gracefully; their tool names are auto-allowed
    # because the assignment is the authorization (the YAML allow-list never
    # names them). A server that fails to connect simply contributes no tools.
    byo_tools: list[AsyncTool] = []
    byo_allow: list[str] = []
    for c in extra_mcp_clients or []:
        await c.connect(strict=False)
        client_tools = c.get_tools()
        byo_tools.extend(client_tools)
        byo_allow.extend(t.name for t in client_tools)
        mcp_clients.append(c)

    all_tools: list[AsyncTool] = [*builtins, *mcp_tools, *byo_tools, *(extra_tools or [])]

    _logger.info(
        "build_default_toolbox composed",
        persona_id=persona.persona_id or "<unknown>",
        builtin_count=len(builtins),
        mcp_tool_count=len(mcp_tools),
        byo_mcp_tool_count=len(byo_tools),
        extra_tool_count=len(extra_tools or []),
        allow_list_size=len(persona.tools),
    )

    # The allow-list: the persona's declared tools PLUS the assigned BYO tool
    # names. When the persona declares nothing (dev-permissive None path) BYO
    # tools are allowed anyway (all-allowed), preserving prior behaviour exactly.
    allow_list = [*persona.tools, *byo_allow] if persona.tools else None
    toolbox = Toolbox(all_tools, allow_list=allow_list)
    return toolbox, mcp_clients
