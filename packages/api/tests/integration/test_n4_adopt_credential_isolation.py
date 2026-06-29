"""Spec N4 B2 — adopted-app credential: encrypted at rest, never emitted, exfil-safe.

N4 reuses the Spec-30 store (``user_mcp_servers``) for the per-user adoption credential,
adding ``catalog_source`` provenance (N4-D-9). Against real Postgres (migration 023
applied via ``migrated_engine``), this proves the credentialed WRITE path's criterion-3
properties for an ADOPTED app:

- the secret is ENCRYPTED at rest (raw column ≠ plaintext, decrypts back);
- it is NEVER returned by any public read surface (only ``has_credential``);
- ``catalog_source`` provenance is persisted + surfaced (the self-extended marker);
- the adversarial exfil angle: NO public store/API read surface emits the plaintext — the
  only decrypt path is the internal, connect-only ``decrypted_servers_for_persona`` (the
  runtime injects it at transport, never the model). The conversation-loop
  prompt-injection-exfil attempt is Group E (E2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from cryptography.fernet import Fernet
from persona_api.config import APIConfig
from persona_api.mcp import store as mcp_store
from persona_api.middleware.rls_context import current_user_id, make_rls_engine
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

_SECRET = "adopted-app-token-do-not-leak"  # noqa: S105 — test sentinel, not a real credential


def _config() -> APIConfig:
    return APIConfig(mcp_credential_key=Fernet.generate_key().decode())


def _ensure_user(engine: Engine, owner: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
            {"i": owner, "e": f"{owner}@x"},
        )


def test_adopted_credential_encrypted_at_rest_and_provenance_persisted(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_store, "assert_url_allowed", lambda _url: None)  # skip DNS
    engine = make_rls_engine(migrated_engine.url.render_as_string(hide_password=False))
    config = _config()
    owner = "user_n4_adopt"

    tok = current_user_id.set(owner)
    try:
        _ensure_user(engine, owner)
        detail = mcp_store.create_server(
            rls_engine=engine,
            config=config,
            owner_id=owner,
            name="notion-remote",
            url="https://mcp.notion.com/mcp",
            auth_method="bearer",
            credential=_SECRET,
            catalog_source="notion-remote",
        )
        server_id = detail["id"]

        # provenance surfaced; credential redacted.
        assert detail["catalog_source"] == "notion-remote"
        assert detail["has_credential"] is True
        assert "credential" not in detail

        # encrypted at rest: raw column ≠ plaintext, decrypts back; provenance persisted.
        with engine.begin() as conn:
            raw = (
                conn.execute(
                    text(
                        "SELECT credentials_encrypted, catalog_source "
                        "FROM user_mcp_servers WHERE id = :i"
                    ),
                    {"i": server_id},
                )
                .mappings()
                .first()
            )
        assert raw is not None
        assert _SECRET not in str(raw["credentials_encrypted"])
        cipher = mcp_store.cipher_from_config(config)
        assert cipher is not None
        assert cipher.decrypt(str(raw["credentials_encrypted"])) == _SECRET
        assert raw["catalog_source"] == "notion-remote"
    finally:
        current_user_id.reset(tok)


def test_no_public_read_surface_emits_the_adopted_credential(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The exfil-safety half at the store layer: every PUBLIC read surface redacts the
    # secret. The plaintext is reachable ONLY via the internal connect-only decrypt.
    monkeypatch.setattr(mcp_store, "assert_url_allowed", lambda _url: None)
    engine = make_rls_engine(migrated_engine.url.render_as_string(hide_password=False))
    config = _config()
    owner = "user_n4_exfil"

    tok = current_user_id.set(owner)
    try:
        _ensure_user(engine, owner)
        detail = mcp_store.create_server(
            rls_engine=engine,
            config=config,
            owner_id=owner,
            name="adopted",
            url="https://adopted.example/mcp",
            auth_method="bearer",
            credential=_SECRET,
            catalog_source="adopted",
        )
        server_id = detail["id"]

        # NONE of the public read surfaces carry the plaintext.
        surfaces = [
            str(detail),
            str(mcp_store.get_server(rls_engine=engine, server_id=server_id)),
            str(mcp_store.list_servers(rls_engine=engine)),
        ]
        assert all(_SECRET not in s for s in surfaces), (
            "credential leaked into a public read surface"
        )

        # ...yet the secret IS stored (the only decrypt is the connect-only internal path).
        cipher = mcp_store.cipher_from_config(config)
        assert cipher is not None
        with engine.begin() as conn:
            raw = conn.execute(
                text("SELECT credentials_encrypted FROM user_mcp_servers WHERE id = :i"),
                {"i": server_id},
            ).scalar_one()
        assert cipher.decrypt(str(raw)) == _SECRET
    finally:
        current_user_id.reset(tok)


def test_manual_byo_has_null_catalog_source(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Backward-compat: a manually-added BYO server (no catalog_source) stays NULL.
    monkeypatch.setattr(mcp_store, "assert_url_allowed", lambda _url: None)
    engine = make_rls_engine(migrated_engine.url.render_as_string(hide_password=False))
    config = _config()
    owner = "user_n4_manual"

    tok = current_user_id.set(owner)
    try:
        _ensure_user(engine, owner)
        detail = mcp_store.create_server(
            rls_engine=engine,
            config=config,
            owner_id=owner,
            name="manual",
            url="https://manual.example/mcp",
            auth_method="none",
            credential=None,
        )
        assert detail["catalog_source"] is None
        with engine.begin() as conn:
            cs = conn.execute(
                text("SELECT catalog_source FROM user_mcp_servers WHERE id = :i"),
                {"i": detail["id"]},
            ).scalar_one()
        assert cs is None
    finally:
        current_user_id.reset(tok)
