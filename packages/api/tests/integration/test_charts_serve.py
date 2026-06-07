"""Spec 17 T05 — chart serve-surface verification (D-17-2 + V4).

Verifies that a chart file landed at ``<workspace>/<owner>/<persona>/charts/<id>.png``
by Spec 17's D-17-X-bytes-persistence is served unchanged by the existing
``GET /v1/personas/:id/uploads/{ref:path}`` route. The contract relies on
two shipped pieces:

- Starlette's ``:path`` converter accepts forward slashes — so the URL
  ``/v1/personas/<pid>/uploads/charts/<id>.png`` carries ``charts/<id>.png``
  as the bound ``ref`` (verified V4).
- ``image_service.fetch:300`` slash-aware ref logic:
  ``relative = ref if "/" in ref else f"{_UPLOAD_DIR_NAME}/{ref}"`` — a ref
  containing ``/`` bypasses the ``uploads/`` prefix injection, resolving
  directly to ``<workspace>/charts/<id>.png`` (the path Spec 16 D-16-5 +
  Spec 17 D-17-2 + D-17-X-charts-path-source lock together).

Cross-tenant access returns 404 (RLS pre-flight via the route's
``_ensure_persona_visible``). Path traversal attempts blocked by the
sandbox resolver.

**Why this is a separate test from Spec 13 T11's upload-roundtrip.** The
upload roundtrip exercises the POST → hash-and-rename → GET path
(``uploads/<blake2b>.<ext>`` content-addressed). T05 exercises the
sandbox-produced path (``charts/<id>.png`` model-named, written by D-17-X
persister, NOT via the upload endpoint). Same GET route, different
producer; the slash-aware ref logic is the seam that lets one route serve
both.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from persona_api.app import create_app
from persona_api.auth import AuthenticatedUser
from persona_api.config import APIConfig
from persona_api.middleware.rls_context import make_rls_engine
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

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

# Minimum-valid 1x1 RGB PNG — same fixture as test_uploads.py uses for
# upload tests; PNG passes the _media_type_for_ext gate transparently.
_TINY_PNG: bytes = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae"
    "426082"
)


@pytest.fixture
def client(
    migrated_engine: Engine,  # noqa: ARG001 — ensures schema + grants
    embedder: HashEmbedder384,
    tmp_path: Path,
) -> Iterator[tuple[TestClient, str, str, Path]]:
    """Real FastAPI client + two users for cross-tenant tests."""
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

    user_a, user_b = "user_t05_a", "user_t05_b"
    with TestClient(app) as c:
        app.state.verify_token = _fake_verify
        app.state.embedder = embedder
        # Drop the lifespan-installed TierRegistry so the persona-detail
        # capabilities surface doesn't lazily instantiate a real chat backend
        # (AuthenticationError("missing API key") on CI without ANTHROPIC_API_KEY).
        if hasattr(app.state, "tier_registry"):
            app.state.tier_registry = None
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            for u in (user_a, user_b):
                conn.execute(
                    text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
                    {"i": u, "e": f"{u}@x.test"},
                )
        su.dispose()
        yield c, user_a, user_b, workspace_root
        su = make_rls_engine(os.environ["DATABASE_URL"])
        with su.begin() as conn:
            conn.execute(
                text("DELETE FROM users WHERE id IN (:a, :b)"),
                {"a": user_a, "b": user_b},
            )
        su.dispose()


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}"}


def _create_persona(c: TestClient, user_id: str) -> str:
    resp = c.post("/v1/personas", json={"yaml": _VALID_YAML}, headers=_auth(user_id))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _seed_chart(
    workspace_root: Path, owner_id: str, persona_id: str, name: str, payload: bytes
) -> Path:
    """Land a chart at <workspace>/<owner>/<persona>/charts/<name>.png — the
    path Spec 17's produced-file persister writes to (D-17-X-bytes-persistence)."""
    chart_path = workspace_root / owner_id / persona_id / "charts" / name
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    chart_path.write_bytes(payload)
    return chart_path


class TestChartServeSlashAwareRef:
    """V4 contract: ``charts/<id>.png`` ref bypasses uploads/ prefix injection."""

    def test_get_chart_returns_bytes_via_slash_aware_ref(
        self,
        client: tuple[TestClient, str, str, Path],
    ) -> None:
        """The D-17-2 + D-17-X-charts-path-source path is servable as-is.

        ``GET /v1/personas/<pid>/uploads/charts/sales-trend.png``:
        the ``:path`` converter binds ``ref="charts/sales-trend.png"`` (V4);
        ``image_service.fetch:300`` slash-aware logic resolves to
        ``<workspace>/<owner>/<persona>/charts/sales-trend.png`` (NOT
        ``<workspace>/<owner>/<persona>/uploads/charts/sales-trend.png``).
        """
        c, uid_a, _uid_b, workspace_root = client
        pid = _create_persona(c, uid_a)
        chart_file = _seed_chart(workspace_root, uid_a, pid, "sales-trend.png", _TINY_PNG)

        resp = c.get(
            f"/v1/personas/{pid}/uploads/charts/sales-trend.png",
            headers=_auth(uid_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == chart_file.read_bytes()

    def test_uploads_charts_double_prefix_also_works(
        self,
        client: tuple[TestClient, str, str, Path],
    ) -> None:
        """Defence-in-depth check: a file ACTUALLY landed at
        ``<workspace>/uploads/charts/<id>.png`` is also reachable. This
        verifies the slash-aware logic is symmetric — the producer chose
        ``charts/<id>.png`` per D-17-2 (cleaner), but the route doesn't
        block the redundant-prefix path either.
        """
        c, uid_a, _uid_b, workspace_root = client
        pid = _create_persona(c, uid_a)
        # Land at <workspace>/<owner>/<persona>/uploads/charts/<id>.png explicitly
        path = workspace_root / uid_a / pid / "uploads" / "charts" / "alt.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_TINY_PNG)

        resp = c.get(
            f"/v1/personas/{pid}/uploads/uploads/charts/alt.png",
            headers=_auth(uid_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.content == _TINY_PNG


class TestChartServeCrossTenantIsolation:
    """RLS pre-flight via the route's persona-visibility check returns 404
    for cross-tenant access — existence-disclosure-safe per D-13."""

    def test_cross_tenant_get_chart_returns_404(
        self,
        client: tuple[TestClient, str, str, Path],
    ) -> None:
        c, uid_a, uid_b, workspace_root = client
        pid = _create_persona(c, uid_a)
        # User A's persona has a chart. User B tries to fetch it.
        _seed_chart(workspace_root, uid_a, pid, "private.png", _TINY_PNG)

        resp = c.get(
            f"/v1/personas/{pid}/uploads/charts/private.png",
            headers=_auth(uid_b),
        )
        assert resp.status_code == 404

    def test_other_tenant_workspace_chart_not_reachable(
        self,
        client: tuple[TestClient, str, str, Path],
    ) -> None:
        """Even if attacker knows the exact path, the persona-visibility
        check rejects before image_service.fetch is called."""
        c, uid_a, uid_b, workspace_root = client
        pid = _create_persona(c, uid_a)
        # Land a chart under user_b's workspace dir directly.
        _seed_chart(workspace_root, uid_b, pid, "owned.png", _TINY_PNG)

        # User A asks for the chart via A's persona → 404 (image_service.fetch
        # resolves to A's workspace, not B's; file not found).
        resp = c.get(
            f"/v1/personas/{pid}/uploads/charts/owned.png",
            headers=_auth(uid_a),
        )
        assert resp.status_code == 404


class TestChartServePathTraversalBlocked:
    """The sandbox resolver rejects traversal attempts as 404 — the route
    inherits this guard automatically via image_service.fetch.
    """

    def test_traversal_via_charts_prefix_returns_404(
        self,
        client: tuple[TestClient, str, str, Path],
    ) -> None:
        c, uid_a, _uid_b, _ws = client
        pid = _create_persona(c, uid_a)
        # ../../../etc/passwd via charts/ prefix → sandbox resolver rejects.
        resp = c.get(
            f"/v1/personas/{pid}/uploads/charts/..%2F..%2F..%2Fetc%2Fpasswd",
            headers=_auth(uid_a),
        )
        assert resp.status_code == 404

    def test_missing_chart_returns_404(
        self,
        client: tuple[TestClient, str, str, Path],
    ) -> None:
        """Non-existent chart ref returns 404 (existence-disclosure-safe)."""
        c, uid_a, _uid_b, _ws = client
        pid = _create_persona(c, uid_a)
        resp = c.get(
            f"/v1/personas/{pid}/uploads/charts/never-existed.png",
            headers=_auth(uid_a),
        )
        assert resp.status_code == 404
