"""Tool-consent integration-safety gate (spec 26 T11).

Confirms the two properties the spec's acceptance criterion #5 + the Phase-4
ruling require, against a real Postgres:

1. The ``persona_self`` audit write SATISFIES the self_facts policy contract
   (``force=True`` + confidence ≥ 0.8 + a meaningful reason). The store's
   FORCE_ONLY + threshold + requires_reason policy REJECTS anything weaker — so a
   write that lands at all is the contract being honoured. We additionally read
   the chunk back and assert source / reason / confidence.
2. The allow-list mutation PERSISTS (re-read YAML shows the tool) and the audit
   is in a VERSIONED, ROLLBACK-capable store (Spec 01) — demonstrated by writing
   a second version to the same logical chain and rolling back to v1.

No model is called; the embedder is the deterministic hash embedder.
"""

# ruff: noqa: ANN401, ARG001
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from persona.audit import JSONLAuditLogger
from persona.schema.chunks import ChunkProvenance, PersonaChunk, WriteSource
from persona.stores import SelfFactsStore
from persona.stores.postgres import PostgresBackend
from persona_api.middleware.rls_context import current_user_id, make_rls_engine
from persona_api.services import persona_service, tool_consent_service
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

_YAML = """\
schema_version: "1.0"
identity:
  name: Astrid
  role: assistant
  background: A helper for the tool-consent gate test.
tools:
  - web_search
"""


def _self_facts_store(
    engine: Engine, embedder: HashEmbedder384, audit_root: Path
) -> SelfFactsStore:
    return SelfFactsStore(
        backend=PostgresBackend(engine=engine, embedder=embedder),
        audit_logger=JSONLAuditLogger(audit_root),
    )


def _read_tools(engine: Engine, persona_id: str) -> list[str]:
    with engine.begin() as conn:
        row = (
            conn.execute(text("SELECT yaml FROM personas WHERE id = :i"), {"i": persona_id})
            .mappings()
            .first()
        )
    assert row is not None
    return list(yaml.safe_load(str(row["yaml"]))["tools"])


def test_tool_consent_gate(
    migrated_engine: Engine,  # ensures schema built
    embedder: HashEmbedder384,
    tmp_path: Path,
) -> None:
    su_url = migrated_engine.url.render_as_string(hide_password=False)
    engine = make_rls_engine(su_url)
    owner = "user_consent_gate"
    audit_root = tmp_path / "audit"
    now = datetime.now(UTC)

    token = current_user_id.set(owner)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id, email) VALUES (:i, :e) ON CONFLICT DO NOTHING"),
                {"i": owner, "e": f"{owner}@x"},
            )
        persona_id = persona_service.create_persona(
            rls_engine=engine,
            embedder=embedder,
            audit_root=audit_root,
            owner_id=owner,
            yaml_str=_YAML,
        )
        # Pre-condition: the persona does NOT have calculator.
        assert "calculator" not in _read_tools(engine, persona_id)

        # --- grant ---
        granted = tool_consent_service.grant_tool_consent(
            rls_engine=engine,
            embedder=embedder,
            audit_root=audit_root,
            persona_id=persona_id,
            owner_id=owner,
            tool_name="calculator",
            written_by=owner,
            now=now,
            turn_index=3,
        )
        assert granted is True

        # (2a) allow-list mutation PERSISTS.
        tools_after = _read_tools(engine, persona_id)
        assert "calculator" in tools_after
        assert "web_search" in tools_after  # existing tool preserved

        # (1) persona_self audit write satisfied the policy contract.
        store = _self_facts_store(engine, embedder, audit_root)
        chain = store.history(persona_id, "tool_consent::calculator")
        assert len(chain) == 1
        head = chain[-1]
        assert head.provenance is not None
        assert head.provenance.source is WriteSource.PERSONA_SELF
        assert head.provenance.reason
        assert "calculator" in head.provenance.reason
        assert "turn 3" in head.provenance.reason
        # confidence stamped ≥ 0.8 (the policy would have rejected < 0.8).
        assert float(head.metadata["confidence"]) >= 0.8

        # idempotent: re-granting is a no-op (no second version).
        again = tool_consent_service.grant_tool_consent(
            rls_engine=engine,
            embedder=embedder,
            audit_root=audit_root,
            persona_id=persona_id,
            owner_id=owner,
            tool_name="calculator",
            written_by=owner,
            now=now,
        )
        assert again is False
        assert len(store.history(persona_id, "tool_consent::calculator")) == 1

        # (2b) the audit chain is VERSIONED + ROLLBACK-capable (Spec 01). Append a
        # second version to the same logical chain, then roll back to v1.
        logical_id = "tool_consent::calculator"
        store.write(
            persona_id,
            [
                PersonaChunk(
                    id=f"{persona_id}::self_facts::tool_consent::calculator::0002",
                    text="Re-confirmed the 'calculator' tool via user consent.",
                    metadata={"tool": "calculator", "confidence": "0.95"},
                    created_at=now,
                    provenance=ChunkProvenance(
                        source=WriteSource.PERSONA_SELF,
                        logical_id=logical_id,
                        written_at=now,
                        written_by=owner,
                        reason="re-confirmation",
                    ),
                )
            ],
            source=WriteSource.PERSONA_SELF,
            written_by=owner,
            reason="re-confirmation",
            force=True,
        )
        assert len(store.history(persona_id, logical_id)) == 2
        # Rollback is itself append-only (Spec 01): it appends a fresh head whose
        # content restores the target version. Succeeding without error + the new
        # head carrying v1's content is "rollback works".
        store.rollback(
            persona_id,
            logical_id,
            to_version=1,
            source=WriteSource.USER,
            written_by=owner,
        )
        chain_after = store.history(persona_id, logical_id)
        assert len(chain_after) == 3  # append-only rollback grew the chain
        assert chain_after[-1].text == "Enabled the 'calculator' tool via user consent."
    finally:
        current_user_id.reset(token)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": owner})
        engine.dispose()
