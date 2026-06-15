"""Integration tests for build-time avatar auto-generation (Spec 29 T4).

Real FastAPI ``TestClient`` + real Postgres (skips when ``APP_DATABASE_URL``
is unset). Covers the four acceptance behaviours end-to-end through the
``POST /v1/personas`` create hook (D-29-3) with a fake ``image_backend`` wired
on ``app.state``:

1. **Auto-generated at build** — create with no ``avatar_url`` sets it to the
   served uploads path; the bytes land on disk at the D-13-4 layout.
2. **Fail-soft** — a backend that is absent / rejects / errors leaves
   ``avatar_url=null`` and the create still returns 201.
3. **User wins** — a create that supplies ``avatar_url`` skips auto-gen; and a
   PATCH after auto-gen overrides the generated avatar.
4. **Free** — no credit-ledger movement for the build-time generation (D-29-2).
"""

# ruff: noqa: ANN401, ARG001, ARG002, E501
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from persona.imagegen import (
    ContentRejectedError,
    GeneratedImage,
    GenerationResult,
    ImageGenOptions,
    ImageProviderError,
)
from persona_api.app import create_app
from persona_api.auth import AuthenticatedUser
from persona_api.config import APIConfig
from persona_api.db.models import credit_transactions as credit_tx_t
from persona_api.middleware.rls_context import make_rls_engine
from sqlalchemy import select, text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from persona.imagegen.result import ImageMediaType
    from sqlalchemy import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration


_VALID_YAML = """\
schema_version: "1.0"
identity:
  name: Astrid
  role: Norwegian tenancy law assistant
  background: |
    Helps tenants understand husleieloven.
  language_default: en
  constraints: []
"""

# Minimum-valid 1x1 RGB PNG (mirrors the imagegen service/route tests).
_TINY_PNG: bytes = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae"
    "426082"
)
_PNG_REF = hashlib.blake2b(_TINY_PNG, digest_size=16).hexdigest()

_USER = "u_persona_avatar"


class _HappyBackend:
    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-1"

    async def generate(
        self, prompt: str, *, options: ImageGenOptions | None = None
    ) -> GenerationResult:
        media: ImageMediaType = "image/png"
        return GenerationResult(
            images=[
                GeneratedImage(
                    image_bytes=_TINY_PNG,
                    workspace_path=None,
                    media_type=media,
                    width=1,
                    height=1,
                    revised_prompt=None,
                )
            ],
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=5.0,
        )

    async def edit(self, *a: object, **k: object) -> GenerationResult:
        raise NotImplementedError


class _RejectingBackend(_HappyBackend):
    async def generate(
        self, prompt: str, *, options: ImageGenOptions | None = None
    ) -> GenerationResult:
        raise ContentRejectedError("nope", context={"reason": "provider_moderation"})


class _ProviderErrorBackend(_HappyBackend):
    async def generate(
        self, prompt: str, *, options: ImageGenOptions | None = None
    ) -> GenerationResult:
        raise ImageProviderError("5xx", context={"reason": "transient"})


@pytest.fixture
def client(
    migrated_engine: Engine,
    embedder: HashEmbedder384,
    tmp_path: Path,
) -> Iterator[tuple[TestClient, str, Path, Engine]]:
    import os

    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        pytest.skip("APP_DATABASE_URL not set")

    workspace_root = tmp_path / "workspace"
    cfg = APIConfig(
        app_database_url=app_url,
        audit_root=str(tmp_path / "audit"),
        workspace_root=workspace_root,
    )
    app = create_app(cfg)

    async def _fake_verify(token: str) -> AuthenticatedUser:
        return AuthenticatedUser(id=token, email=None)

    with TestClient(app) as c:
        app.state.verify_token = _fake_verify
        app.state.embedder = embedder
        if hasattr(app.state, "tier_registry"):
            app.state.tier_registry = None
        app.state.image_backend = _HappyBackend()  # per-test override below

        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
                {"i": _USER, "e": f"{_USER}@x.test"},
            )
        yield c, _USER, workspace_root, su
        with su.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": _USER})
        su.dispose()


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}"}


def _create(c: TestClient, *, avatar_url: str | None = None) -> dict:
    body: dict[str, object] = {"yaml": _VALID_YAML}
    if avatar_url is not None:
        body["avatar_url"] = avatar_url
    resp = c.post("/v1/personas", json=body, headers=_auth(_USER))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Auto-generated at build.
# ---------------------------------------------------------------------------


def test_create_auto_generates_avatar_and_sets_served_url(
    client: tuple[TestClient, str, Path, Engine],
) -> None:
    c, _uid, workspace_root, _su = client
    detail = _create(c)
    pid = detail["id"]

    expected = f"/v1/personas/{pid}/uploads/uploads/{_PNG_REF}.png"
    assert detail["avatar_url"] == expected
    # Persisted + visible via GET.
    assert c.get(f"/v1/personas/{pid}", headers=_auth(_USER)).json()["avatar_url"] == expected
    # Bytes on disk at the D-13-4 layout.
    on_disk = workspace_root / _USER / pid / "uploads" / f"{_PNG_REF}.png"
    assert on_disk.is_file()
    assert on_disk.read_bytes() == _TINY_PNG


def test_auto_generated_avatar_serves_through_uploads_route(
    client: tuple[TestClient, str, Path, Engine],
) -> None:
    c, _uid, _ws, _su = client
    detail = _create(c)
    avatar_url = detail["avatar_url"]
    assert avatar_url is not None
    resp = c.get(avatar_url, headers=_auth(_USER))
    assert resp.status_code == 200
    assert resp.content == _TINY_PNG


def test_auto_avatar_gen_is_free_no_ledger_movement(
    client: tuple[TestClient, str, Path, Engine],
) -> None:
    c, _uid, _ws, su = client
    _create(c)
    with su.begin() as conn:
        rows = conn.execute(select(credit_tx_t.c.delta).where(credit_tx_t.c.user_id == _USER)).all()
    assert rows == [], f"build-time avatar gen must not touch credits; got {rows}"


# ---------------------------------------------------------------------------
# 2. Fail-soft — avatar_url stays null, create still 201.
# ---------------------------------------------------------------------------


def test_create_fails_soft_when_backend_absent(
    client: tuple[TestClient, str, Path, Engine],
) -> None:
    c, _uid, _ws, _su = client
    c.app.state.image_backend = None  # type: ignore[attr-defined]
    detail = _create(c)
    assert detail["avatar_url"] is None


def test_create_fails_soft_on_content_rejection(
    client: tuple[TestClient, str, Path, Engine],
) -> None:
    c, _uid, _ws, _su = client
    c.app.state.image_backend = _RejectingBackend()  # type: ignore[attr-defined]
    detail = _create(c)
    assert detail["avatar_url"] is None


def test_create_fails_soft_on_provider_error(
    client: tuple[TestClient, str, Path, Engine],
) -> None:
    c, _uid, _ws, _su = client
    c.app.state.image_backend = _ProviderErrorBackend()  # type: ignore[attr-defined]
    detail = _create(c)
    assert detail["avatar_url"] is None


# ---------------------------------------------------------------------------
# 3. User wins — supplied avatar skips auto-gen; PATCH overrides generated.
# ---------------------------------------------------------------------------


def test_user_supplied_avatar_skips_auto_generation(
    client: tuple[TestClient, str, Path, Engine],
) -> None:
    c, _uid, workspace_root, _su = client
    detail = _create(c, avatar_url="https://cdn.test/mine.png")
    assert detail["avatar_url"] == "https://cdn.test/mine.png"
    # Auto-gen did not run → no generated bytes on disk for this persona.
    uploads = workspace_root / _USER / detail["id"] / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_patch_overrides_generated_avatar(
    client: tuple[TestClient, str, Path, Engine],
) -> None:
    c, _uid, _ws, _su = client
    detail = _create(c)
    pid = detail["id"]
    assert detail["avatar_url"] is not None
    assert detail["avatar_url"].startswith("/v1/personas/")

    c.patch(
        f"/v1/personas/{pid}",
        json={"yaml": _VALID_YAML, "avatar_url": "https://cdn.test/user.png"},
        headers=_auth(_USER),
    )
    assert c.get(f"/v1/personas/{pid}", headers=_auth(_USER)).json()["avatar_url"] == (
        "https://cdn.test/user.png"
    )
