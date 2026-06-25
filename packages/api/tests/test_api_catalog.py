"""Tools + skills read-only endpoints (spec 08, T13, §5.4).

No DB. Mounts the app with a fake verifier and asserts /v1/tools and /v1/skills
return the built-in tools + bundled skills as name/description lists, and require
auth.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from persona_api.app import create_app
from persona_api.auth import AuthenticatedUser
from persona_api.config import APIConfig


@pytest.fixture
def client() -> TestClient:
    app = create_app(APIConfig())  # no DB needed for the catalog routes

    async def _verify(token: str) -> AuthenticatedUser:
        return AuthenticatedUser(id=token, email=None)

    app.state.verify_token = _verify
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer u1"}


def test_list_tools(client: TestClient) -> None:
    resp = client.get("/v1/tools", headers=_auth())
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    # Every built-in tool factory the runtime wires up — see catalog_service.
    # Authoring constrains the LLM to "names from AVAILABLE only"; a tool
    # missing here is silently invisible to the wizard.
    assert {
        "web_search",
        "web_fetch",
        "file_read",
        "file_write",
        "code_execution",
        "generate_image",
        # Spec 26 T08 — the new built-ins must also surface in authoring so the
        # wizard can offer them (sourced from persona-core TOOL_CATALOG).
        "calculator",
        "datetime",
        "regex_match",
        "json_query",
        "text_diff",
        "currency_convert",
        "text_summarize",
    } <= names
    # each has a non-empty description
    assert all(t["description"] for t in resp.json())


def test_list_skills(client: TestClient) -> None:
    resp = client.get("/v1/skills", headers=_auth())
    assert resp.status_code == 200
    names = {s["name"] for s in resp.json()}
    # Every folder under persona/skills/builtin must be declared in the
    # catalog — otherwise the authoring wizard can't suggest the skill.
    # Spec 24 (D-24-1): the 5 document-format packs folded into the single
    # document_generation skill (deprecated names still resolve via the alias
    # shim, but the catalog surfaces only the live folders).
    assert {
        "code_review",
        "data_analysis",
        "document_generation",
        "web_research",
    } <= names
    # The deleted document-format skills must NOT appear as separate entries.
    assert not (
        {
            "document_drafting",
            "docx_generation",
            "pdf_generation",
            "pptx_generation",
            "xlsx_generation",
        }
        & names
    )


def test_tools_requires_auth(client: TestClient) -> None:
    assert client.get("/v1/tools").status_code == 401


def test_skills_requires_auth(client: TestClient) -> None:
    assert client.get("/v1/skills").status_code == 401


# -- N1 (D-N1-3): /v1/mcp-catalog = builtin floor + Docker mirror -------------

_BUILTINS = {"time", "calculator", "filesystem", "weather", "fetch", "github"}


def test_mcp_catalog_legacy_contract_unchanged_and_fields_additive(client: TestClient) -> None:
    """The spec-30 five-field contract is intact; N1 display fields ride defaults.

    A client written against spec 30 (name/description/provider/default_enabled/
    required_env) sees no break — the new fields are additive-with-default, so the
    builtin rows carry empty/neutral defaults.
    """
    resp = client.get("/v1/mcp-catalog", headers=_auth())
    assert resp.status_code == 200
    rows = {r["name"]: r for r in resp.json()}
    assert set(rows) >= _BUILTINS  # builtin floor always present (no mirror needed)

    fs = rows["filesystem"]
    # legacy spec-30 contract intact
    assert {"name", "description", "provider", "default_enabled", "required_env"} <= set(fs)
    assert fs["default_enabled"] is True
    assert fs["provider"] == "mcp:builtin"
    # additive N1 fields present, defaulted for a builtin row
    assert fs["display_name"] == ""
    assert fs["icon_url"] == ""
    assert fs["server_type"] == "builtin"
    assert fs["signed"] is False
    assert fs["allow_hosts"] == []
    assert fs["secrets"] == []


def test_mcp_catalog_secret_schema_is_display_only(client: TestClient) -> None:
    """D-N1-5 at the API boundary: the secret schema exposes no value field."""
    schema = client.get("/openapi.json").json()
    secret = schema["components"]["schemas"]["MCPCatalogSecret"]["properties"]
    assert set(secret) == {"name", "env", "example", "description"}
    assert "value" not in secret
    assert "credential" not in secret


def test_mcp_catalog_requires_auth(client: TestClient) -> None:
    assert client.get("/v1/mcp-catalog").status_code == 401


def test_merged_catalog_no_mirror_is_exactly_the_builtins() -> None:
    """No mirror snapshot → load_mirror_catalog falls back to builtin → just the 6."""
    from persona_api.services import catalog_service

    names = {e.name for e in catalog_service.merged_mcp_catalog()}
    assert names == _BUILTINS


def test_merged_catalog_builtin_floor_with_builtin_wins_on_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Builtin is the floor; a same-named mirror entry is superseded; tail is unioned."""
    from persona.tools.mcp.catalog import MCPCatalog, MCPServerCatalogEntry
    from persona_api.services import catalog_service

    fake = MCPCatalog(
        servers={
            # collides with the authored builtin "github" → builtin must win
            "github": MCPServerCatalogEntry(
                name="github", description="MIRROR github", kind="external", risk="high"
            ),
            # long-tail mirror entry → unioned in
            "notion-mirror": MCPServerCatalogEntry(
                name="notion-mirror",
                description="Notion",
                kind="external",
                risk="medium",
                display_name="Notion",
            ),
        }
    )
    monkeypatch.setattr(catalog_service, "load_mirror_catalog", lambda: fake)
    merged = {e.name: e for e in catalog_service.merged_mcp_catalog()}

    assert set(merged) >= _BUILTINS  # the floor survives
    assert merged["github"].description != "MIRROR github"  # builtin-wins (authored)
    assert merged["notion-mirror"].display_name == "Notion"  # tail unioned
    # deterministic order: builtins first, then the new mirror name
    names = [e.name for e in catalog_service.merged_mcp_catalog()]
    assert names[: len(_BUILTINS)] == [
        "time",
        "calculator",
        "filesystem",
        "weather",
        "fetch",
        "github",
    ]
    assert names[-1] == "notion-mirror"
