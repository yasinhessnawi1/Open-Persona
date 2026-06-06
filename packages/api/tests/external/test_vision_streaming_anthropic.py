"""Spec 13 T17 — Anthropic streaming multimodal smoke (SCAFFOLD only).

``@pytest.mark.external`` — skipped by default per the workspace pyproject's
``addopts = "-v --tb=short -m 'not integration and not external'"``. Run
manually with::

    docker start persona-pg
    set -a; . ./.env; set +a            # ANTHROPIC_API_KEY
    uv run pytest -m external -k vision_streaming_anthropic -v

Per spec 13 Phase 2 fold-in #9 this is a SCAFFOLD: the live execution is
manual, paid, and non-deterministic and is intentionally outside CI. The
captured response + assertion outcome are appended to
[`docs/specs/phase2/spec_13/state.md`](../../../../docs/specs/phase2/spec_13/state.md)
under "Manual smoke results"; Phase 6 close-out reads that section as the
deep proof for criterion #4 (Tension #6: Anthropic-streaming-multimodal
was never exercised by spec 11's soak path).

What this test verifies end-to-end against a *real* Anthropic API:

1. ``POST /v1/personas/{persona_id}/uploads`` (T11) accepts the fixture
   PNG and returns a workspace ``ref``.
2. ``POST /v1/conversations/{conversation_id}/messages`` (the chat SSE
   path, spec 08) drives ``ConversationLoop.turn`` with a multimodal
   ``ConversationMessage`` whose content list carries an
   :class:`persona.schema.content.ImageContent` referencing the uploaded
   ``ref`` (D-13-X-now option c — refs travel via the message body, never
   inline base64 in the row).
3. The SSE stream completes cleanly:
   * No mid-stream ``error`` event frames.
   * ``chunk`` event ``delta`` strings concat to a coherent description
     of the fixture (mentions ``triangle`` and/or ``PERSONA TEST 13``).
   * Terminal ``done`` event fires with a populated ``usage`` block
     (``prompt_tokens > 0`` AND ``completion_tokens > 0``).
   * ``done.tier`` matches the routed vision-capable Anthropic tier
     (typically ``"frontier"`` — the router's real choice surfaces via
     :func:`persona_api.services.chat_service.stream_chat`'s ``on_event``
     ``tier`` event, then rides the ``done`` frame).

The fixture at ``packages/api/tests/fixtures/vision_test_image.png`` is the
shared T16 PNG (a 512x512 red triangle with the text ``PERSONA TEST 13``).

TODO (Phase 6 candidate — fold-in #9 / T17 acceptance tighten):
  - error-mid-stream case: force an Anthropic 5xx mid-stream (or feed a
    malformed image ref) and assert the SSE stream surfaces a clean
    ``error`` event frame BEFORE ``done`` rather than dropping the
    connection mid-frame. The v0.1 ``stream_chat`` shape (
    ``packages/api/src/persona_api/services/chat_service.py``) currently
    re-raises into the StreamingResponse generator — confirm whether the
    framing survives or needs a dedicated ``error`` SSE event before
    asserting on it here. Logged in
    ``docs/specs/phase2/spec_13/state.md`` as a Phase 6 tighten candidate.
  - ``done.usage`` field-presence assertion (not just value-presence):
    confirm the dict-keys shape stays ``{"prompt_tokens", "completion_
    tokens"}`` after any future ``usage`` enrichment (cache hits,
    server-side tool time). Move the assertion to a richer schema match
    if the field set grows.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytestmark = pytest.mark.external


_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_IMAGE_PATH = _FIXTURES_DIR / "vision_test_image.png"
_EXAMPLES = _REPO_ROOT / "packages" / "core" / "examples"

# Case-insensitive identifiable tokens the reconstructed reply must mention.
# Mirrors T16's matcher list — one match is enough to prove the model saw
# the bytes (non-determinism tolerance: wording varies by call).
_IDENTIFIABLE_TOKENS = (
    "triangle",
    "red",
    "persona test 13",
    "persona test",
    "test 13",
)

# D-13-3: Anthropic's vision capability matrix is "all" — every Anthropic
# model accepts ImageContent. Use the configured frontier tier (sonnet 4.6
# by default per BackendConfig). The router's real choice surfaces on the
# ``done`` event via the ``tier`` callback in ``stream_chat``.
_EXPECTED_VISION_TIER = "frontier"
_ANTHROPIC_MODEL_DEFAULT = "claude-sonnet-4-6"


if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- #
# environment: route all tiers → Anthropic, set DB URLs (parity w/ soak)      #
# --------------------------------------------------------------------------- #
def _anthropic_env() -> str:
    """Force the soak DB URLs and route every tier to Anthropic.

    Returns the API key (already validated as non-empty). Skips the test
    cleanly when no key is configured.
    """
    os.environ["DATABASE_URL"] = "postgresql+psycopg://persona:persona@localhost:5436/persona"
    os.environ["APP_DATABASE_URL"] = (
        "postgresql+psycopg://persona_app:persona_app@localhost:5436/persona"
    )
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PERSONA_FRONTIER_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set; skipping live Anthropic streaming smoke")
    for tier in ("FRONTIER", "MID", "SMALL"):
        os.environ[f"PERSONA_{tier}_PROVIDER"] = "anthropic"
        os.environ[f"PERSONA_{tier}_MODEL"] = _ANTHROPIC_MODEL_DEFAULT
        os.environ[f"PERSONA_{tier}_API_KEY"] = key
    os.environ["PERSONA_PROVIDER"] = "anthropic"
    os.environ["PERSONA_MODEL"] = _ANTHROPIC_MODEL_DEFAULT
    os.environ["PERSONA_API_KEY"] = key
    return key


def _superuser_engine() -> Any:  # noqa: ANN401
    from persona_api.middleware.rls_context import make_rls_engine

    return make_rls_engine(os.environ["DATABASE_URL"])


@pytest.fixture
def vision_client(tmp_path: Path) -> Iterator[tuple[TestClient, str, str]]:
    """Yield (client, user_id, persona_id) wired to the real Anthropic stack.

    Mirrors the soak fixture's pattern (D-11-13 fake-auth seam, real
    everything else). The workspace_root is pointed at ``tmp_path`` so
    the upload route's image_service writes land there, isolated from
    other runs.
    """
    _anthropic_env()
    from fastapi.testclient import TestClient
    from persona_api.app import create_app
    from persona_api.auth import AuthenticatedUser
    from persona_api.config import APIConfig
    from sqlalchemy import text

    persona_yaml_file = os.environ.get("VISION_PERSONA", "astrid_tenancy_law")
    yaml_text = (_EXAMPLES / f"{persona_yaml_file}.yaml").read_text(encoding="utf-8")

    cfg = APIConfig(
        app_database_url=os.environ["APP_DATABASE_URL"],
        database_url=os.environ["DATABASE_URL"],
        audit_root=str(tmp_path / "audit"),
        workspace_root=str(tmp_path / "workspace"),
    )
    app = create_app(cfg)

    async def _fake_verify(token: str) -> AuthenticatedUser:
        return AuthenticatedUser(id=token, email=None)

    user_id = f"vision_{int(time.time())}"
    with TestClient(app) as client:
        app.state.verify_token = _fake_verify
        if getattr(app.state, "build_conversation_loop", None) is None:
            pytest.skip("no real loop wired (TierRegistry unconfigured) — check the env")
        su = _superuser_engine()
        with su.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
                {"i": user_id, "e": f"{user_id}@vision.test"},
            )
        su.dispose()
        resp = client.post(
            "/v1/personas",
            json={"yaml": yaml_text},
            headers={"Authorization": f"Bearer {user_id}"},
        )
        assert resp.status_code == 201, resp.text
        persona_id = resp.json()["id"]
        yield client, user_id, persona_id
        su = _superuser_engine()
        with su.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        su.dispose()


def _auth(uid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {uid}"}


def _upload_fixture(client: TestClient, uid: str, persona_id: str) -> str:
    """Upload the T16 fixture image and return the workspace ref."""
    assert _IMAGE_PATH.is_file(), (
        f"fixture missing on disk: {_IMAGE_PATH} — regenerate via the T16 "
        "fixture-generation snippet in the spec-13 handover."
    )
    with _IMAGE_PATH.open("rb") as fh:
        files = {"file": ("vision_test_image.png", fh, "image/png")}
        resp = client.post(f"/v1/personas/{persona_id}/uploads", files=files, headers=_auth(uid))
    assert resp.status_code == 201, f"upload failed: {resp.status_code} {resp.text}"
    body = resp.json()
    ref = body["workspace_path"]
    assert isinstance(ref, str), f"upload returned non-string workspace_path: {ref!r}"
    assert ref, "upload returned empty workspace_path"
    return ref


def _new_conversation(client: TestClient, uid: str, persona_id: str) -> str:
    resp = client.post(
        f"/v1/personas/{persona_id}/conversations",
        json={"title": "vision-smoke"},
        headers=_auth(uid),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _parse_sse_stream(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE response body into ``[(event_name, data_json), ...]``.

    Mirrors ``persona_api.services.chat_service._sse``'s wire shape:
    ``event: <name>\\ndata: <json>\\n\\n``. Tolerates blank lines and
    leading/trailing whitespace.
    """
    events: list[tuple[str, dict[str, Any]]] = []
    current_event: str | None = None
    for line in raw.splitlines():
        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            payload = line.removeprefix("data:").strip()
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if current_event is not None and isinstance(data, dict):
                events.append((current_event, data))
                current_event = None
    return events


# --------------------------------------------------------------------------- #
# the smoke                                                                    #
# --------------------------------------------------------------------------- #
def test_anthropic_streaming_vision_describes_fixture(
    vision_client: tuple[TestClient, str, str],
) -> None:
    """Live SSE round-trip: upload → POST messages with image ref → consume done.

    The streaming contract this test pins (fold-in #9 / T17 acceptance):

    * SSE stream completes; no mid-stream ``error`` event frames.
    * ``chunk`` ``delta`` strings concat to a description that mentions
      at least one of :data:`_IDENTIFIABLE_TOKENS` (case-insensitive).
    * Terminal ``done`` event has a populated ``usage`` block
      (``prompt_tokens > 0`` and ``completion_tokens > 0``).
    * ``done.tier`` matches the routed vision-capable Anthropic tier.
    """
    client, uid, persona_id = vision_client

    # 1) Upload the fixture (T11 route).
    ref = _upload_fixture(client, uid, persona_id)

    # 2) Start a fresh conversation.
    conv_id = _new_conversation(client, uid, persona_id)

    # 3) POST the multimodal turn over the chat SSE path.
    # D-13-X-now option (c): image refs travel via the ``images`` array on
    # the message body. T20 wired ``PostMessageRequest.images`` so this no
    # longer 422s — the chat body carries refs cleanly through the route +
    # chat_service + _persist_turn pipeline.
    body: dict[str, Any] = {
        "content": (
            "Describe this image in one short sentence. Mention any text or shapes you see."
        ),
        "images": [{"workspace_path": ref, "media_type": "image/png"}],
    }
    resp = client.post(
        f"/v1/conversations/{conv_id}/messages",
        json=body,
        headers=_auth(uid),
    )
    assert resp.status_code == 200, f"messages POST failed: {resp.status_code} {resp.text}"

    # 4) Parse the SSE stream the StreamingResponse returned.
    events = _parse_sse_stream(resp.text)
    assert events, "SSE stream yielded no events"

    # 4a) No mid-stream error frames (the runtime's error path emits an
    # ``error`` event when it surfaces a domain failure; bare connection
    # drops would already have raised at resp.text).
    error_events = [data for name, data in events if name == "error"]
    assert not error_events, f"mid-stream error frame(s): {error_events!r}"

    # 4b) Reconstruct the reply from chunk deltas.
    chunk_events = [data for name, data in events if name == "chunk"]
    assert chunk_events, "no ``chunk`` events on the stream"
    reply = "".join(str(c.get("delta", "")) for c in chunk_events)
    assert reply.strip(), "reconstructed reply is empty"

    haystack = reply.lower()
    matched = [tok for tok in _IDENTIFIABLE_TOKENS if tok in haystack]
    assert matched, (
        f"streamed reply did not mention any of {list(_IDENTIFIABLE_TOKENS)} — "
        f"Anthropic likely did not see the image bytes. Reply was: {reply!r}"
    )

    # 4c) Terminal ``done`` event with a populated ``usage`` block and the
    # routed vision-capable tier.
    done_events = [data for name, data in events if name == "done"]
    assert done_events, "no terminal ``done`` event on the stream"
    done = done_events[-1]

    usage = done.get("usage") or {}
    assert isinstance(usage, dict), f"done.usage is not a dict: {usage!r}"
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    assert prompt_tokens > 0, f"done.usage.prompt_tokens not populated: {usage!r}"
    assert completion_tokens > 0, f"done.usage.completion_tokens not populated: {usage!r}"

    tier = done.get("tier")
    assert tier == _EXPECTED_VISION_TIER, (
        f"done.tier={tier!r} did not match the routed vision tier "
        f"({_EXPECTED_VISION_TIER!r}); check the runtime router + the "
        "D-13-3 vision capability matrix."
    )
