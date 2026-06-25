"""Spec N1 T5 — gateway credential-isolation proof (D-N1-5).

The load-bearing N1 security property + the N4 foundation: the gateway bearer flows
operator → transport → gateway, and reaches NO model-facing surface — not the prompt's
tool specs, not the tool-call args, not the tool result, not the audit log. This proves
it **adversarially**: the secret IS used for transport (so the assertion is meaningful,
not vacuous), yet appears in NONE of the model-facing artifacts (a whole-trace grep
asserts zero hits). ``config.secrets`` is shown display-only (never resolved into an
injected credential); the per-user injection path is a reserved N4 seam, not live.
"""

# ruff: noqa: ANN401 — the MCP SDK fakes are intentionally Any-typed
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from persona.config import PersonaCoreConfig
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolCall
from persona.tools import build_default_toolbox
from persona.tools.audit import MemoryToolAuditLogger

if TYPE_CHECKING:
    from pathlib import Path

_SECRET = "gw-super-secret-bearer-do-not-leak"  # noqa: S105 — test sentinel, not a real credential

#: Headers the (faked) transport actually received — proving the bearer reaches the wire.
_captured_headers: list[dict[str, str] | None] = []


@asynccontextmanager
async def _capturing_transport(_url: str, **kwargs: Any) -> Any:
    _captured_headers.append(kwargs.get("headers"))
    yield (object(), object(), object())


@asynccontextmanager
async def _fake_session(_r: Any, _w: Any) -> Any:
    yield SimpleNamespace(
        initialize=AsyncMock(),
        list_tools=AsyncMock(
            return_value=SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="search",
                        description="Search the web",
                        inputSchema={"type": "object"},
                    )
                ]
            )
        ),
        call_tool=AsyncMock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(text="result: found 3 items")],
                structuredContent=None,
                isError=False,
            )
        ),
    )


def _persona() -> Persona:
    return Persona(
        persona_id="iso-test",
        identity=PersonaIdentity(
            name="T", role="R", background="A persona for the credential-isolation proof."
        ),
        tools=["mcp:docker:search"],  # opted in to the gateway tool
    )


@pytest.fixture
def patch_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp
    import mcp.client.streamable_http as shttp

    _captured_headers.clear()
    monkeypatch.setattr(shttp, "streamablehttp_client", _capturing_transport)
    monkeypatch.setattr(mcp, "ClientSession", _fake_session)


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_mcp")
async def test_bearer_reaches_transport_but_no_model_facing_surface(tmp_path: Path) -> None:
    config = PersonaCoreConfig(
        tools_sandbox_root=tmp_path,
        docker_mcp_gateway_url="http://gw.internal:8811/mcp",
        docker_mcp_gateway_token=_SECRET,
    )
    audit = MemoryToolAuditLogger()
    toolbox, clients = await build_default_toolbox(config, _persona(), tool_audit_logger=audit)

    # (1) the bearer DID reach the transport — so its absence elsewhere is meaningful.
    assert _captured_headers, "the gateway transport was never opened"
    assert any(
        h is not None and h.get("Authorization") == f"Bearer {_SECRET}" for h in _captured_headers
    )

    # (2) assemble EVERY model-facing surface the gateway tool contributes.
    surfaces: list[str] = []
    surfaces += [s.model_dump_json() for s in toolbox.get_specs()]  # (a) tool specs
    call = ToolCall(name="mcp:docker:search", args={"q": "hello"})  # (b) tool-call args
    result = await toolbox.dispatch(call)  # (c) the tool result
    surfaces.append(call.model_dump_json())
    surfaces.append(result.model_dump_json())
    surfaces += [  # (d) the audit log
        json.dumps({"action": e.action, "tool": e.tool_name, "meta": e.metadata})
        for e in audit.events
    ]

    # the dispatch really ran (the result surface is real, not skipped)
    assert "found 3 items" in result.content
    assert "mcp:docker:search" in toolbox.names()

    # (3) the adversarial grep: the secret appears in NONE of the surfaces.
    leaks = [s for s in surfaces if _SECRET in s]
    assert leaks == [], f"bearer leaked into {len(leaks)} model-facing surface(s)"

    for c in clients:
        await c.disconnect()


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_mcp")
async def test_catalog_secrets_are_display_only_no_token_no_injected_header(
    tmp_path: Path,
) -> None:
    # The connect path derives the header ONLY from the operator token — never from any
    # catalog ``config.secrets`` schema. With no token, there is no Authorization header
    # at all, proving the mirror's secret schema is display-only (never resolved into an
    # injected credential; per-user injection is the reserved N4 seam).
    config = PersonaCoreConfig(
        tools_sandbox_root=tmp_path,
        docker_mcp_gateway_url="http://gw.internal:8811/mcp",
        # no docker_mcp_gateway_token
    )
    _toolbox, clients = await build_default_toolbox(config, _persona())
    assert _captured_headers, "the gateway transport was never opened"
    assert all(h is None for h in _captured_headers)  # no auth header injected

    for c in clients:
        await c.disconnect()


def test_n4_per_user_injection_is_a_reserved_seam_not_wired() -> None:
    # DEFINED (a runtime-checkable Protocol with the documented method) but NOT BUILT —
    # no implementation/caller in the live connect path (A2 on_event-seam discipline).
    import inspect

    import persona.tools._factory as factory
    from persona.tools.mcp.gateway_secrets import GatewaySecretResolver

    assert hasattr(GatewaySecretResolver, "resolve")
    src = inspect.getsource(factory)
    assert "GatewaySecretResolver" not in src
    assert "gateway_secrets" not in src
