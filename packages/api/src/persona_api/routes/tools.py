"""Tools + skills read-only endpoints (spec 08, T13, §5.4).

``GET /v1/tools`` and ``GET /v1/skills`` — platform-global name+description
lists for the web authoring flow's checkboxes. Authenticated (consistent with
the rest of the surface) but not RLS-scoped (no tenant data).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from persona_api.auth import AuthenticatedUser, get_current_user
from persona_api.schemas import MCPCatalogServer, ToolSummary
from persona_api.services import catalog_service

router = APIRouter(prefix="/v1", tags=["catalog"])

__all__ = ["router"]


@router.get("/tools", response_model=list[ToolSummary])
async def list_tools(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> list[ToolSummary]:
    """List the available tools (name + description)."""
    return [ToolSummary(name=n, description=d) for n, d in catalog_service.list_tools()]


@router.get("/skills", response_model=list[ToolSummary])
async def list_skills(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> list[ToolSummary]:
    """List the available skills (name + description)."""
    return [ToolSummary(name=n, description=d) for n, d in catalog_service.list_skills()]


@router.get("/mcp-catalog", response_model=list[MCPCatalogServer])
async def list_mcp_catalog(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> list[MCPCatalogServer]:
    """List the built-in MCP servers (spec 30 T11) for the capability-management UI."""
    return [
        MCPCatalogServer(
            name=name,
            description=description,
            provider=provider,
            default_enabled=default_enabled,
            required_env=required_env,
        )
        for name, description, provider, default_enabled, required_env in (
            catalog_service.list_mcp_servers()
        )
    ]
