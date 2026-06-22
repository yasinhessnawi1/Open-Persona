"""Integration test: the routing → delivery → audit chain on Postgres (Spec C0, T6).

Routes an originated message through the real :class:`DeliveryRouter` to the real
:class:`WebAppDeliverer` (no live session) and asserts the outcome is ``pending``
(present-on-next-open, never dropped) and that BOTH audit layers land — the routing
decision (``origination.routing``) and the channel delivery (``origination.delivery.pending``)
— with no message row written by the delivery path (persistence is the recorder's job).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from persona.schema.origination import OriginatedMessage, PersonaIdentityTag
from persona_api.db.models import audit_log as audit_log_t
from persona_api.db.models import messages as messages_t
from persona_api.middleware.rls_context import current_user_id, make_rls_engine
from persona_api.services.delivery_router import DeliveryRouter
from persona_api.services.web_deliverer import WebAppDeliverer
from sqlalchemy import func, select

if TYPE_CHECKING:
    from collections.abc import Iterator

    from persona.schema.origination import OriginatedMessage as _Msg
    from sqlalchemy import Engine

pytestmark = pytest.mark.integration

UTC_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


class _NoLiveSessions:
    def lookup(self, message: _Msg) -> None:  # noqa: ARG002 — Protocol signature
        return None


@pytest.fixture
def router(migrated_engine: Engine, database_url: str) -> Iterator[tuple[DeliveryRouter, Engine]]:
    rls_engine = make_rls_engine(database_url)
    web = WebAppDeliverer(rls_engine=rls_engine, sessions=_NoLiveSessions())
    router = DeliveryRouter(deliverers={"web": web}, rls_engine=rls_engine)
    try:
        yield router, migrated_engine
    finally:
        rls_engine.dispose()
        current_user_id.set(None)


@pytest.mark.asyncio
async def test_routes_to_web_home_records_both_audit_layers(
    router: tuple[DeliveryRouter, Engine],
) -> None:
    delivery_router, superuser = router
    message = OriginatedMessage(
        persona=PersonaIdentityTag(persona_id="pa", display_name="A"),
        owner_user_id="user_a",
        content="I've finished.",
        conversation_id="conv_x",
        created_at=UTC_NOW,
    )

    result = await delivery_router.deliver(message)
    assert result.outcome.value == "pending"
    assert result.channel == "web"

    with superuser.begin() as conn:
        actions = [
            r["action"]
            for r in conn.execute(
                select(audit_log_t.c.action).where(audit_log_t.c.target == "conv_x")
            )
            .mappings()
            .all()
        ]
        msg_count = conn.execute(select(func.count()).select_from(messages_t)).scalar_one()

    # both audit layers present: the routing decision and the channel delivery.
    assert "origination.routing" in actions
    assert "origination.delivery.pending" in actions
    # routing/delivery persist no message row (the recorder owns persistence).
    assert msg_count == 0
