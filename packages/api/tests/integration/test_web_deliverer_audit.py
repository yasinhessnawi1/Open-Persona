"""Integration test: the web deliverer's pending path records a real audit row
(Spec C0, T5, D-C0-5 / D-C0-X-no-push-broker) on Postgres.

Proves the no-session path is observable end-to-end: no live stream → the message
(already persisted by the T4 recorder) stays present-on-next-open and the delivery
outcome lands in ``audit_log`` as ``origination.delivery.pending`` — never silently
dropped. The deliverer itself writes NO message row (persistence is the recorder's
job — clean separation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from persona.schema.origination import OriginatedMessage, PersonaIdentityTag
from persona_api.db.models import audit_log as audit_log_t
from persona_api.db.models import messages as messages_t
from persona_api.middleware.rls_context import current_user_id, make_rls_engine
from persona_api.services.web_deliverer import WebAppDeliverer
from sqlalchemy import func, select

if TYPE_CHECKING:
    from collections.abc import Iterator

    from persona.schema.origination import OriginatedMessage as _Msg
    from sqlalchemy import Engine

pytestmark = pytest.mark.integration

UTC_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


class _NoLiveSessions:
    """Registry with no open streams → every delivery is pending."""

    def lookup(self, message: _Msg) -> None:  # noqa: ARG002 — Protocol signature
        return None


@pytest.fixture
def web_deliverer(
    migrated_engine: Engine, database_url: str
) -> Iterator[tuple[WebAppDeliverer, Engine]]:
    rls_engine = make_rls_engine(database_url)
    deliverer = WebAppDeliverer(rls_engine=rls_engine, sessions=_NoLiveSessions())
    try:
        yield deliverer, migrated_engine
    finally:
        rls_engine.dispose()
        current_user_id.set(None)


@pytest.mark.asyncio
async def test_pending_delivery_writes_audit_row_and_no_message(
    web_deliverer: tuple[WebAppDeliverer, Engine],
) -> None:
    deliverer, superuser = web_deliverer
    message = OriginatedMessage(
        persona=PersonaIdentityTag(persona_id="pa", display_name="A"),
        owner_user_id="user_a",
        content="I've finished.",
        conversation_id="conv_x",
        created_at=UTC_NOW,
    )

    result = await deliverer.deliver(message)
    assert result.outcome.value == "pending"

    with superuser.begin() as conn:
        rows = (
            conn.execute(
                select(audit_log_t).where(audit_log_t.c.action == "origination.delivery.pending")
            )
            .mappings()
            .all()
        )
        msg_count = conn.execute(select(func.count()).select_from(messages_t)).scalar_one()

    assert len(rows) == 1
    assert rows[0]["user_id"] == "user_a"
    assert rows[0]["target"] == "conv_x"
    # the deliverer persists NO message row — persistence is the recorder's job (T4).
    assert msg_count == 0
