"""Integration tests for the originated-message recorder on real Postgres (Spec C0, T4).

Proves the load-bearing T4 contract end-to-end against the real schema (built by
``alembic upgrade head``, which includes migration 013's ``messages.originated``
column + the 001 RLS policies):

* happy path — an originated message is persisted as a first-class ``assistant``
  row with ``originated=true`` (DB discriminator) + written to episodic memory
  (criterion 2), starting a conversation when none is given (D-C0-3);
* the ADVERSARIAL cross-tenant case (criterion 9) — a persona may originate ONLY
  to its owner; a mismatched owner raises ``OriginationForbiddenError`` and writes
  NOTHING (no half-write: the record raises before any row is inserted, so the
  ``Originator`` never reaches delivery).

Requires ``DATABASE_URL`` pointing at a disposable ``*_test`` database (the
``migrated_engine`` fixture DROP/CREATE-s the schema). Skipped otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from persona.audit import MemoryAuditLogger
from persona.errors import OriginationForbiddenError
from persona.schema.origination import OriginatedMessage, PersonaIdentityTag
from persona.stores.episodic import EpisodicStore
from persona.stores.postgres import PostgresBackend
from persona_api.config import Edition
from persona_api.db.models import messages as messages_t
from persona_api.middleware.rls_context import current_user_id, make_rls_engine
from persona_api.services.origination import OriginationRecorder
from sqlalchemy import func, select, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine
    from tests.conftest import HashEmbedder384

pytestmark = pytest.mark.integration

UTC_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


def _seed_two_tenants(superuser_engine: Engine) -> None:
    with superuser_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email) VALUES "
                "('user_a','a@example.com'),('user_b','b@example.com')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO personas (id, owner_id, yaml) VALUES "
                "('pa','user_a','name: a'),('pb','user_b','name: b')"
            )
        )


@pytest.fixture
def recorder(
    migrated_engine: Engine, database_url: str, embedder: HashEmbedder384
) -> Iterator[tuple[OriginationRecorder, Engine]]:
    """The recorder over a cloud-edition RLS engine, plus the superuser engine for
    assertions/seeding. ``migrated_engine`` builds the schema (with 013 + RLS)."""
    _seed_two_tenants(migrated_engine)
    rls_engine = make_rls_engine(database_url)
    backend = PostgresBackend(engine=rls_engine, embedder=embedder)
    episodic = EpisodicStore(backend=backend, audit_logger=MemoryAuditLogger())
    rec = OriginationRecorder(rls_engine=rls_engine, episodic_store=episodic, edition=Edition.cloud)
    try:
        yield rec, migrated_engine
    finally:
        rls_engine.dispose()
        current_user_id.set(None)


def _message(
    *, persona_id: str, owner_user_id: str, conversation_id: str | None = None
) -> OriginatedMessage:
    return OriginatedMessage(
        persona=PersonaIdentityTag(persona_id=persona_id, display_name="A"),
        owner_user_id=owner_user_id,
        content="I've finished the report you asked for.",
        conversation_id=conversation_id,
        created_at=UTC_NOW,
    )


def _message_rows(superuser_engine: Engine) -> list[dict[str, object]]:
    with superuser_engine.begin() as conn:
        return [dict(r) for r in conn.execute(select(messages_t)).mappings().all()]


def _episodic_count(superuser_engine: Engine, persona_id: str) -> int:
    with superuser_engine.begin() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM memory_chunks "
                    "WHERE persona_id = :pid AND kind = 'episodic'"
                ),
                {"pid": persona_id},
            ).scalar_one()
        )


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_originated_message_persists_as_assistant_row_with_marker(
        self, recorder: tuple[OriginationRecorder, Engine]
    ) -> None:
        rec, superuser = recorder
        conversation_id = await rec.record(_message(persona_id="pa", owner_user_id="user_a"))

        rows = _message_rows(superuser)
        assert len(rows) == 1
        row = rows[0]
        assert row["role"] == "assistant"  # no new role
        assert row["originated"] is True  # the DB discriminator (migration 013)
        assert row["conversation_id"] == conversation_id  # started one (D-C0-3)
        assert row["content"] == "I've finished the report you asked for."

    @pytest.mark.asyncio
    async def test_writes_assistant_only_episodic_chunk(
        self, recorder: tuple[OriginationRecorder, Engine]
    ) -> None:
        rec, superuser = recorder
        await rec.record(_message(persona_id="pa", owner_user_id="user_a"))
        assert _episodic_count(superuser, "pa") == 1  # remembered reaching out (crit 2)

    @pytest.mark.asyncio
    async def test_attaches_to_existing_conversation_when_supplied(
        self, recorder: tuple[OriginationRecorder, Engine]
    ) -> None:
        rec, superuser = recorder
        # start one origination → reuse its conversation for the second
        first_conv = await rec.record(_message(persona_id="pa", owner_user_id="user_a"))
        second_conv = await rec.record(
            _message(persona_id="pa", owner_user_id="user_a", conversation_id=first_conv)
        )
        assert second_conv == first_conv
        rows = _message_rows(superuser)
        assert {r["conversation_id"] for r in rows} == {first_conv}
        assert len(rows) == 2


class TestAdversarialCrossTenant:
    """Criterion 9 — a persona can originate ONLY to its owner; no half-write."""

    @pytest.mark.asyncio
    async def test_cross_tenant_target_raises_origination_forbidden(
        self, recorder: tuple[OriginationRecorder, Engine]
    ) -> None:
        rec, _ = recorder
        with pytest.raises(OriginationForbiddenError):
            # persona pa is owned by user_a; targeting user_b must be refused.
            await rec.record(_message(persona_id="pa", owner_user_id="user_b"))

    @pytest.mark.asyncio
    async def test_cross_tenant_writes_nothing_no_half_write(
        self, recorder: tuple[OriginationRecorder, Engine]
    ) -> None:
        rec, superuser = recorder
        with pytest.raises(OriginationForbiddenError):
            await rec.record(_message(persona_id="pa", owner_user_id="user_b"))
        # the record raised BEFORE any insert → zero message rows, zero episodic.
        assert _message_rows(superuser) == []
        assert _episodic_count(superuser, "pa") == 0
        with superuser.begin() as conn:
            conv_count = conn.execute(
                select(func.count()).select_from(text("conversations"))
            ).scalar_one()
        assert conv_count == 0

    @pytest.mark.asyncio
    async def test_other_tenants_persona_cannot_be_targeted(
        self, recorder: tuple[OriginationRecorder, Engine]
    ) -> None:
        """user_a cannot originate via user_b's persona pb (the inverse mismatch)."""
        rec, superuser = recorder
        with pytest.raises(OriginationForbiddenError):
            await rec.record(_message(persona_id="pb", owner_user_id="user_a"))
        assert _message_rows(superuser) == []
