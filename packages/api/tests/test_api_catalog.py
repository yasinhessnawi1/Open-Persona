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
    } <= names
    # each has a non-empty description
    assert all(t["description"] for t in resp.json())


def test_list_skills(client: TestClient) -> None:
    resp = client.get("/v1/skills", headers=_auth())
    assert resp.status_code == 200
    names = {s["name"] for s in resp.json()}
    # Every folder under persona/skills/builtin must be declared in the
    # catalog — otherwise the authoring wizard can't suggest the skill.
    assert {
        "data_analysis",
        "document_drafting",
        "docx_generation",
        "pdf_generation",
        "pptx_generation",
        "web_research",
        "xlsx_generation",
    } <= names


def test_tools_requires_auth(client: TestClient) -> None:
    assert client.get("/v1/tools").status_code == 401


def test_skills_requires_auth(client: TestClient) -> None:
    assert client.get("/v1/skills").status_code == 401
