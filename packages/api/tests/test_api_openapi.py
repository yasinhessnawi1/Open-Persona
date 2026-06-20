"""OpenAPI sanity (spec 08, T14, acceptance #12).

The web app (spec 09) generates its TypeScript client from FastAPI's OpenAPI
spec. We can't run the TS codegen here, so the proof is a well-formed
``/openapi.json`` with the approved ``channel``/``format_hints`` additions
serialising with correct optionality/nullability, and the endpoint surface
present.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from persona_api.app import create_app
from persona_api.config import APIConfig


@pytest.fixture
def spec() -> dict:
    app = create_app(APIConfig())
    with TestClient(app) as c:
        resp = c.get("/openapi.json")
        assert resp.status_code == 200
        return resp.json()


def test_openapi_is_well_formed(spec: dict) -> None:
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "Persona API"
    assert "paths" in spec
    assert "components" in spec


def test_core_endpoints_present(spec: dict) -> None:
    paths = spec["paths"]
    assert "/v1/personas" in paths
    assert "/v1/personas/{persona_id}/conversations" in paths
    assert "/v1/conversations/{conversation_id}/messages" in paths
    assert "/v1/personas/{persona_id}/runs" in paths
    assert "/v1/runs/{run_id}/events" in paths
    assert "/v1/me/credits" in paths
    assert "/healthz" in paths
    assert "/livez" in paths


def test_channel_context_is_nullable_optional_on_message_request(spec: dict) -> None:
    schemas = spec["components"]["schemas"]
    assert "PostMessageRequest" in schemas
    assert "ChannelContext" in schemas
    props = schemas["PostMessageRequest"]["properties"]
    assert "channel" in props
    # channel is optional (not in required) and nullable (anyOf with null, or
    # a $ref union with null) — the web-UI case sends none.
    required = schemas["PostMessageRequest"].get("required", [])
    assert "channel" not in required
    # the field permits null (anyOf containing a null type, FastAPI/pydantic v2 shape)
    channel_schema = props["channel"]
    assert "anyOf" in channel_schema or channel_schema.get("nullable") is True


def test_channel_context_fields(spec: dict) -> None:
    cc = spec["components"]["schemas"]["ChannelContext"]["properties"]
    assert "platform" in cc
    assert "platform_user_id" in cc
    assert "platform_chat_id" in cc
    assert "metadata" in cc
    # platform is required (the only required field); it's a free-form string
    required = spec["components"]["schemas"]["ChannelContext"].get("required", [])
    assert "platform" in required


def test_done_event_format_hints_at_model_level(spec: dict) -> None:
    # The SSE event payloads (DoneEvent etc.) are serialised manually into the
    # `data:` field, not declared as FastAPI response_models, so they're not in
    # components.schemas — OpenAPI doesn't model SSE event streams. The
    # format_hints guarantee (D-08-3) is therefore asserted at the model level;
    # the web app imports/mirrors these Pydantic models directly.
    assert "DoneEvent" not in spec["components"]["schemas"]
    from persona_api.schemas import DoneEvent

    done = DoneEvent(tier="frontier")
    assert done.format_hints == {}
    assert "format_hints" in done.model_dump()


def test_conversation_summary_last_message_fields_optional_nullable(spec: dict) -> None:
    # The web sidebar reads ConversationSummary off the LIST endpoint; the two
    # last-message fields must be optional (not required) and nullable in the
    # generated TS client (a conversation with no messages yields null).
    schemas = spec["components"]["schemas"]
    assert "ConversationSummary" in schemas
    props = schemas["ConversationSummary"]["properties"]
    assert "last_message_preview" in props
    assert "last_message_role" in props
    required = schemas["ConversationSummary"].get("required", [])
    assert "last_message_preview" not in required
    assert "last_message_role" not in required
    # both permit null (FastAPI/pydantic-v2 anyOf-with-null shape)
    for field in ("last_message_preview", "last_message_role"):
        schema = props[field]
        assert "anyOf" in schema or schema.get("nullable") is True, field
