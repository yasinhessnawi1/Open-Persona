"""Unit tests for async persona-create enrichment (off-critical-path side effects).

The performance fix moves voice auto-pick + avatar generation OFF the create
request's critical path into a FastAPI ``BackgroundTasks`` job that runs after
the response is sent. These tests prove the three load-bearing properties of
that move WITHOUT Postgres:

  1. ``create_persona`` returns BEFORE the avatar/voice side effects run — the
     handler SCHEDULES a background task, it does not await the enrichment. The
     create response therefore carries ``avatar_url=null`` (F1's default renders;
     the web bounded-polls until the background task fills it in).
  2. ``_enrich_persona_after_create`` runs voice-THEN-avatar (the order the
     synchronous path used: pick the voice while the bearer token is fresh, then
     generate the avatar which can block the full wall-clock budget), and honours
     the ``generate_avatar`` guard (a user-supplied avatar skips avatar gen but
     the voice pick still runs).
  3. The background task re-establishes the owner's RLS scope by setting the
     ``current_user_id`` contextvar — GATED to the CLOUD edition (community runs
     a listener-less single-owner SQLite engine with no RLS, so it must NOT set
     the GUC). The contextvar is reset afterwards so nothing leaks.
"""

# ruff: noqa: ARG001 — the fake hooks must mirror the real keyword signatures
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from persona_api.config import Edition
from persona_api.middleware.rls_context import current_user_id
from persona_api.routes import personas as personas_routes

_OWNER = "u_async"
_PERSONA = "persona_async"
_YAML = "schema_version: '1.0'\nidentity:\n  name: Astrid\n"


def _request(edition: Edition | None) -> SimpleNamespace:
    config = SimpleNamespace(edition=edition) if edition is not None else None
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


@pytest.fixture(autouse=True)
def recorded_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """Replace the two enrichment hooks with recorders of (name, observed-uid).

    Each fake records the ``current_user_id`` contextvar value it sees so the
    test can assert the RLS scope was (cloud) / was not (community) bound when
    the writes would run. Ordering is captured by append order.
    """
    calls: list[tuple[str, str | None]] = []

    async def _fake_voice(
        request: object, *, owner_id: str, persona_id: str, yaml_str: str
    ) -> None:
        calls.append(("voice", current_user_id.get()))

    async def _fake_avatar(
        request: object, *, owner_id: str, persona_id: str, yaml_str: str
    ) -> None:
        calls.append(("avatar", current_user_id.get()))

    monkeypatch.setattr(personas_routes.voice_assignment_service, "maybe_assign_voice", _fake_voice)
    monkeypatch.setattr(personas_routes, "_maybe_generate_avatar", _fake_avatar)
    return calls


def _run_enrich(*, edition: Edition | None, generate_avatar: bool) -> None:
    asyncio.run(
        personas_routes._enrich_persona_after_create(
            _request(edition),  # type: ignore[arg-type]
            owner_id=_OWNER,
            persona_id=_PERSONA,
            yaml_str=_YAML,
            generate_avatar=generate_avatar,
        )
    )


# ---------------------------------------------------------------------------
# Order + guard.
# ---------------------------------------------------------------------------


def test_enrich_runs_voice_then_avatar(recorded_calls: list[tuple[str, str | None]]) -> None:
    _run_enrich(edition=Edition.cloud, generate_avatar=True)
    assert [name for name, _ in recorded_calls] == ["voice", "avatar"]


def test_enrich_skips_avatar_when_user_supplied_one(
    recorded_calls: list[tuple[str, str | None]],
) -> None:
    # generate_avatar=False mirrors `body.avatar_url is not None`: voice still
    # runs (the persona may still need a fitting voice), avatar gen does not.
    _run_enrich(edition=Edition.cloud, generate_avatar=False)
    assert [name for name, _ in recorded_calls] == ["voice"]


# ---------------------------------------------------------------------------
# RLS scope re-establishment — gated to cloud.
# ---------------------------------------------------------------------------


def test_enrich_binds_rls_scope_on_cloud(recorded_calls: list[tuple[str, str | None]]) -> None:
    _run_enrich(edition=Edition.cloud, generate_avatar=True)
    # Both hooks observed the owner bound on the contextvar (the pool checkout
    # listener reads this to run set_config('app.current_user_id', owner_id)).
    assert recorded_calls == [("voice", _OWNER), ("avatar", _OWNER)]


def test_enrich_does_not_bind_rls_scope_on_community(
    recorded_calls: list[tuple[str, str | None]],
) -> None:
    _run_enrich(edition=Edition.community, generate_avatar=True)
    # Community has no RLS (listener-less SQLite): the contextvar stays unset, so
    # the GUC is never set — the same writes run unscoped (single owner).
    assert recorded_calls == [("voice", None), ("avatar", None)]


def test_enrich_resets_contextvar_after_run() -> None:
    # Before + after the cloud run the contextvar must be back to its default —
    # no tenant-id residue leaks past the background task.
    assert current_user_id.get() is None
    _run_enrich(edition=Edition.cloud, generate_avatar=True)
    assert current_user_id.get() is None


# ---------------------------------------------------------------------------
# The handler returns BEFORE the side effects run — it schedules, not awaits.
# ---------------------------------------------------------------------------


def test_create_schedules_background_task_and_returns_before_enrichment(
    monkeypatch: pytest.MonkeyPatch, recorded_calls: list[tuple[str, str | None]]
) -> None:
    """``create_persona`` returns with the task QUEUED, not yet executed.

    A FastAPI ``BackgroundTasks`` only runs after the response is sent. We drive
    the handler directly with a real ``BackgroundTasks`` and assert: (a) the row
    + audit writes happened synchronously, (b) the returned detail has
    ``avatar_url=None`` (the default the web renders until the poll resolves),
    and (c) the enrichment hooks have NOT run yet — they are merely scheduled.
    """
    from fastapi import BackgroundTasks

    monkeypatch.setattr(personas_routes.persona_service, "create_persona", lambda **_: _PERSONA)
    monkeypatch.setattr(personas_routes.audit_service, "record", lambda **_: None)
    monkeypatch.setattr(personas_routes, "_tier_registry", lambda _r: None)
    monkeypatch.setattr(
        personas_routes.persona_service,
        "get_persona",
        lambda **_: {
            "id": _PERSONA,
            "yaml": _YAML,
            "schema_version": "1.0",
            "avatar_url": None,
            "consent_to_auto_dispatch": None,
            "consent_updated_at": None,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    )

    state = SimpleNamespace(
        rls_engine=object(),
        embedder=object(),
        audit_root="/tmp/audit",
        config=SimpleNamespace(edition=Edition.cloud),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    body = SimpleNamespace(yaml=_YAML, avatar_url=None)
    background = BackgroundTasks()
    user = SimpleNamespace(id=_OWNER, email=None)

    detail = asyncio.run(
        personas_routes.create_persona(
            body,  # type: ignore[arg-type]
            request,  # type: ignore[arg-type]
            background,
            user,  # type: ignore[arg-type]
        )
    )

    # Returned the default avatar (null) — the side effects have NOT run.
    assert detail.avatar_url is None
    assert recorded_calls == []
    # …but the enrichment was scheduled to run after the response.
    assert len(background.tasks) == 1

    # Draining the queue (what Starlette does post-response) now runs them.
    asyncio.run(background())
    assert [name for name, _ in recorded_calls] == ["voice", "avatar"]
